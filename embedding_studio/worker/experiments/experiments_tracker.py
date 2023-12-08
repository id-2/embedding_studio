import logging
import os
import subprocess
from socket import setdefaulttimeout
from typing import Dict, List, Optional, Tuple

import mlflow
import numpy as np
import pandas as pd
from mlflow.entities import Experiment

from embedding_studio.embeddings.models.interface import (
    EmbeddingsModelInterface,
)
from embedding_studio.worker.experiments.finetuning_params import (
    FineTuningParams,
)
from embedding_studio.worker.experiments.finetuning_session import (
    EXPERIMENT_PREFIX,
    FineTuningSession,
)
from embedding_studio.worker.experiments.metrics_accumulator import (
    MetricsAccumulator,
    MetricValue,
)
from embedding_studio.worker.mlflow.utils import (
    get_experiment_id_by_name,
    get_run_id_by_name,
)

INITIAL_EXPERIMENT_NAME: str = f"{EXPERIMENT_PREFIX} / initial"
INITIAL_RUN_NAME: str = "initial_model"
DEFAULT_TIMEOUT: int = 120000

# MLFlow upload models using urllib3, if model is heavy enough provided default timeout is not enough
# That's why increase it here. TODO: check from time to time whether this issue is resolved by MLFlow
setdefaulttimeout(DEFAULT_TIMEOUT)
logger = logging.getLogger(__name__)


def _get_base_requirements():
    try:
        logger.info('Generate requirements with poetry')
        # Run the poetry export command
        result = subprocess.run([
            "poetry", "export", f"--directory={os.path.dirname(__file__)}", "--with", "ml", "-f", "requirements.txt", "--without-hashes"
        ], capture_output=True, text=True,
                                check=True)

        # Get the requirements from the standard output
        requirements = result.stdout.strip().split('\n')

        return requirements
    except subprocess.CalledProcessError as e:
        print(f"Error running poetry export: {e}")
        return []


class ExperimentsManager:
    def __init__(
        self,
        tracking_uri: str,
        main_metric: str,
        accumulators: List[MetricsAccumulator],
        is_loss: Optional[bool] = False,
        n_top_runs: Optional[int] = 10,
        requirements: Optional[str] = None,
    ):
        """Wrapper over mlflow package to manage certain fine-tuning experiments.

        :param tracking_uri: url of MLFlow server
        :type tracking_uri: str
        :param main_metric: name of main metric that will be used to find best model
        :type main_metric: str
        :param accumulators: accumulators of metrics to be logged
        :type accumulators: List[MetricsAccumulator]
        :param is_loss: is main metric loss (if True, then best quality is minimal) (default: False)
        :type is_loss:  Optional[bool]
        :param n_top_runs: how many hyper params group consider to be used in following tuning steps (default: 10)
        :type n_top_runs: Optional[int]
        :param requirements: extra requirements to be passed to mlflow.pytorch.log_model (default: None)
        :type requirements: Optional[str]
        """
        if not isinstance(tracking_uri, str) or len(tracking_uri) == 0:
            raise ValueError(
                f"MLFlow tracking URI value should be a not empty string"
            )
        mlflow.set_tracking_uri(tracking_uri)

        if not isinstance(main_metric, str) or len(main_metric) == 0:
            raise ValueError(f"main_metric value should be a not empty string")
        self.main_metric = main_metric
        self._metric_field = f"metrics.{self.main_metric}"

        self._n_top_runs = n_top_runs
        self._is_loss = is_loss

        if len(accumulators) == 0:
            logger.warning(
                "No accumulators were provided, there will be no metrics logged except loss"
            )
        self._accumulators = accumulators

        self._requirements: List[str] = (
            _get_base_requirements() if requirements is None else requirements
        )

        self._experiment = None
        self._tuning_session = None
        self._tuning_session_id = None

        self._run = None
        self._run_params = None
        self._run_id = None

    @property
    def is_loss(self) -> bool:
        return self._is_loss

    def __del__(self):
        self.finish_run()
        self.finish_session()

    def upload_initial_model(self, model: EmbeddingsModelInterface):
        """Upload the very first, initial model to the mlflow server

        :param model: model to be uploaded
        :type model: EmbeddingsModelInterface
        """
        self.finish_session()
        with mlflow.start_run(
            experiment_id=get_experiment_id_by_name(INITIAL_EXPERIMENT_NAME),
            run_name=INITIAL_RUN_NAME,
        ) as run:
            logger.info(
                f"Upload initial model to {INITIAL_EXPERIMENT_NAME} / {INITIAL_RUN_NAME}"
            )
            mlflow.pytorch.log_model(
                model, "model", pip_requirements=self._requirements
            )
            logger.info("Uploading is finished")

    def download_initial_model(self) -> EmbeddingsModelInterface:
        """Download initial model.

        :return: initial embeddings model
        :rtype: EmbeddingsModelInterface
        """
        model_uri: str = f"runs:/{get_run_id_by_name(get_experiment_id_by_name(INITIAL_EXPERIMENT_NAME), INITIAL_RUN_NAME)}/model"
        logger.info(f"Download the model from {model_uri}")
        model = mlflow.pytorch.load_model(model_uri)
        logger.info("Downloading is finished")
        return model

    def get_top_params(self) -> Optional[List[FineTuningParams]]:
        """Get top N previous fine-tuning session best params

        :return: fine-tuning session params
        :rtype: List[FineTuningParams]
        """
        initial_id: Optional[str] = get_experiment_id_by_name(
            INITIAL_EXPERIMENT_NAME
        )
        last_session_id: Optional[str] = self.get_previous_session_id()
        if initial_id == last_session_id:
            logger.warning(
                "Can't retrieve top params, no previous session in history"
            )
            return None

        else:
            custom_filter = (
                "params.model_uploaded = '1'"  # we ignore runs without a model
            )
            runs: pd.DataFrame = mlflow.search_runs(
                experiment_ids=[last_session_id], filter_string=custom_filter
            )
            runs = runs[runs.status == "FINISHED"]  # and only finished ones
            if runs.shape[0] == 0:
                logger.warning(
                    "Can't retrieve top params, no previous session's finished runs with uploaded model in history"
                )
                return None

            # Get the indices that would sort the DataFrame based on the specified parameter
            sorted_indices: np.ndarray = np.argsort(
                runs[self._metric_field].values
            )
            if not self.is_loss:
                sorted_indices = sorted_indices[
                    ::-1
                ]  # Use [::-1] to sort in descending order

            # Extract the top N rows based on the sorted indices
            top_n_rows: np.ndarray = runs.iloc[
                sorted_indices[: self._n_top_runs]
            ]

            # Define a mapping dictionary to remove the "params." prefix
            column_mapping: Dict[str, str] = {
                col: col.replace("params.", "") for col in top_n_rows.columns
            }

            # Rename the columns
            top_n_rows: np.ndarray = top_n_rows.rename(
                columns=column_mapping
            ).to_dict(orient="records")

            return [FineTuningParams(**row) for row in top_n_rows]

    def get_last_model(self) -> EmbeddingsModelInterface:
        """Get previous session best embedding model.

        :return: best embedding model
        :rtype: EmbeddingsModelInterface
        """
        initial_id: Optional[str] = get_experiment_id_by_name(
            INITIAL_EXPERIMENT_NAME
        )
        last_session_id: Optional[str] = self.get_previous_session_id()
        if initial_id == last_session_id or last_session_id is None:
            logger.warning(
                "Download initial model, no previous session in history"
            )
            return self.download_initial_model()
        else:
            run_id, _ = self._get_best_quality(last_session_id)
            if run_id is None:
                logger.warning(
                    "Download initial model, no previous session's finished runs with uploaded model in history"
                )
                return self.download_initial_model()
            else:
                model_uri: str = f"runs:/{run_id}/model"
                logger.info(f"Download the model from {model_uri}")
                model = mlflow.pytorch.load_model(model_uri)
                logger.info("Downloading is finished")
                return model

    def get_previous_session_id(self) -> Optional[str]:
        experiments: List[Experiment] = [
            e
            for e in mlflow.search_experiments()
            if e.name.startswith(EXPERIMENT_PREFIX)
        ]
        if len(experiments) == 0:
            logger.warning("No sessions found")
            return None
        else:
            return max(
                experiments, key=lambda exp: exp.creation_time
            ).experiment_id

    def delete_previous_session(self):
        experiment_id: Optional[str] = self.get_previous_session_id()
        if experiment_id is not None:
            logger.info(
                f"Session with ID {experiment_id} is going to be deleted"
            )
            mlflow.delete_experiment(experiment_id)
        else:
            logger.warning(
                "Can't delete a previous session, no previous session in history"
            )

    def set_session(self, session: FineTuningSession):
        """Start a new fine-tuning session.

        :param session: fine-tuning session info
        :type session:  FineTuningSession
        """
        if self._tuning_session == INITIAL_EXPERIMENT_NAME:
            self.finish_session()

        logger.info("Start a new fine-tuning sessions")

        self._tuning_session = session
        self._tuning_session_id = get_experiment_id_by_name(str(session))
        if self._tuning_session_id is None:
            self._tuning_session_id = mlflow.create_experiment(str(session))

        self._experiment = mlflow.set_experiment(
            experiment_id=self._tuning_session_id
        )

    def set_run(self, params: FineTuningParams):
        """Start a new run with provided fine-tuning params

        :param params: provided fine-tuning params
        :type params: FineTuningParams
        """
        convert_value = (
            lambda value: ", ".join(map(str, value))
            if isinstance(value, list)
            else value
        )

        if self._tuning_session == INITIAL_EXPERIMENT_NAME:
            # TODO: implement exception
            raise ValueError("You can't start run for initial experiment")

        if self._run is not None:
            self.finish_run()

        logger.info(
            f"Start a new run for session {self._tuning_session_id} with params:\n\t{str(params)}"
        )

        self._run_params = params
        run_name: str = self._run_params.id
        self._run_id = get_run_id_by_name(self._tuning_session_id, run_name)
        self._run = mlflow.start_run(
            self._run_id, self._tuning_session_id, run_name
        )
        if self._run_id is None:
            self._run_id = self._run.info.run_id

        for key, value in dict(self._tuning_session).items():
            mlflow.log_param(key, convert_value(value))

        for key, value in dict(self._run_params).items():
            mlflow.log_param(key, convert_value(value))

    def finish_session(self):
        logger.info(f"Finish current session {self._tuning_session_id}")
        self._tuning_session = INITIAL_EXPERIMENT_NAME
        self._tuning_session_id = get_experiment_id_by_name(
            INITIAL_EXPERIMENT_NAME
        )

        if self._tuning_session_id is None:
            self._experiment = mlflow.set_experiment(
                experiment_name=INITIAL_EXPERIMENT_NAME
            )
            self._tuning_session_id = self._experiment.experiment_id
        else:
            self._experiment = mlflow.set_experiment(
                experiment_id=self._tuning_session_id
            )

        logger.info(f"Current session is finished")

    def finish_run(self):
        logger.info(
            f"Finish current run {self._tuning_session_id} / {self._run_id}"
        )
        for accumulator in self._accumulators:
            accumulator.clear()

        mlflow.end_run()

        # Set params to default None
        self._run = None
        self._run_params = None
        self._run_id = None

        logger.info(f"Current run is finished")

    def save_model(
        self, model: EmbeddingsModelInterface, best_only: bool = True
    ):
        """Save fine-tuned embedding model

        :param model: model to be saved
        :type model:  EmbeddingsModelInterface
        :param best_only: save only if it's the best (default: True)
        :type best_only: bool
        """
        if self._tuning_session == INITIAL_EXPERIMENT_NAME:
            raise ValueError(
                f"Can't save not initial model for {INITIAL_EXPERIMENT_NAME} experiment"
            )

        if self._run_id is None:
            raise ValueError("There is no current Run")

        logger.info(
            f"Save model for {self._tuning_session_id} / {self._run_id}"
        )
        if not best_only:
            mlflow.pytorch.log_model(
                model, "model", pip_requirements=self._requirements
            )
            mlflow.log_param("model_uploaded", 1)
            logger.info("Upload is finished")
        else:
            current_quality = self.get_quality()
            best_run_id, best_quality = self.get_best_quality()

            if best_run_id is None or (
                current_quality <= best_quality
                if self.is_loss
                else current_quality >= best_quality
            ):
                mlflow.pytorch.log_model(
                    model, "model", pip_requirements=self._requirements
                )
                mlflow.log_param("model_uploaded", 1)
                logger.info("Upload is finished")

                if best_run_id is not None:
                    pass  # TODO: Delete model
            else:
                logger.info("Not the best run - ignore saving")

    def save_metric(self, metric_value: MetricValue):
        """Accumulate and save metric value

        :param metric_value: value to be logged
        :type metric_value: MetricValue
        """
        for accumulator in self._accumulators:
            for name, value in accumulator.accumulate(metric_value):
                mlflow.log_metric(name, value)

    def get_quality(self) -> float:
        """Current run quality value

        :return: quality value
        :rtype: float
        """
        if self._tuning_session == INITIAL_EXPERIMENT_NAME:
            raise ValueError(
                f"No metrics for {INITIAL_EXPERIMENT_NAME} experiment"
            )

        if self._run_id is None:
            raise ValueError("There is no current Run")

        runs: pd.DataFrame = mlflow.search_runs(
            experiment_ids=[self._tuning_session_id]
        )
        quality: np.ndarray = runs[runs.run_id == self._run_id][
            self._metric_field
        ]
        return float(quality) if quality.shape[0] == 1 else float(quality[0])

    def _get_best_quality(
        self, experiment_id: str
    ) -> Tuple[Optional[str], float]:
        custom_filter: str = (
            "params.model_uploaded = '1'"  # we ignore runs without a model
        )
        runs: pd.DataFrame = mlflow.search_runs(
            experiment_ids=[experiment_id], filter_string=custom_filter
        )
        runs = runs[runs.status == "FINISHED"]  # and not finished ones
        if runs.shape[0] == 0:
            logger.warning(
                "No finished experiments found with model uploaded, except initial"
            )
            return None, 0.0

        else:
            value: float = (
                runs[self._metric_field].min()
                if self.is_loss
                else runs[self._metric_field].max()
            )
            best: pd.DataFrame = runs[runs[self._metric_field] == value][
                ["run_id", self._metric_field]
            ]
            return list(best.itertuples(index=False, name=None))[0]

    def get_best_quality(self) -> Tuple[str, float]:
        """Get current fine-tuning session best quality

        :return: run_id and best metric value
        :rtype: Tuple[str, float]
        """
        if self._tuning_session == INITIAL_EXPERIMENT_NAME:
            raise ValueError(
                f"No metrics for {INITIAL_EXPERIMENT_NAME} experiment"
            )

        return self._get_best_quality(self._tuning_session_id)
