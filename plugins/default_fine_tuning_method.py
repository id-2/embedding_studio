from typing import List

from sentence_transformers import SentenceTransformer

from embedding_studio.core.config import settings
from embedding_studio.core.plugin import FineTuningMethod
from embedding_studio.embeddings.data.clickstream.parsers.s3_parser import (
    AWSS3ClickstreamParser,
)
from embedding_studio.embeddings.data.clickstream.search_event import (
    DummyEventType,
    SearchResult,
)
from embedding_studio.embeddings.data.clickstream.splitter import (
    ClickstreamSessionsSplitter,
)
from embedding_studio.embeddings.data.clickstream.text_query_item import (
    TextQueryItem,
)
from embedding_studio.embeddings.data.clickstream.text_query_retriever import (
    TextQueryRetriever,
)
from embedding_studio.embeddings.data.loaders.s3.s3_loader import (
    AWSS3DataLoader,
)
from embedding_studio.embeddings.data.storages.producers.clip import (
    CLIPItemStorageProducer,
)
from embedding_studio.embeddings.data.utils.fields_normalizer import (
    DatasetFieldsNormalizer,
)
from embedding_studio.embeddings.losses.prob_cosine_margin_ranking_loss import (
    CosineProbMarginRankingLoss,
)
from embedding_studio.embeddings.models.text_to_image.clip import (
    TextToImageCLIPModel,
)
from embedding_studio.models.clickstream.sessions import SessionWithEvents
from embedding_studio.models.plugin import FineTuningBuilder, PluginMeta
from embedding_studio.workers.fine_tuning.data.prepare_data import prepare_data
from embedding_studio.workers.fine_tuning.experiments.experiments_tracker import (
    ExperimentsManager,
)
from embedding_studio.workers.fine_tuning.experiments.finetuning_settings import (
    FineTuningSettings,
)
from embedding_studio.workers.fine_tuning.experiments.initial_params.clip import (
    INITIAL_PARAMS,
)
from embedding_studio.workers.fine_tuning.experiments.metrics_accumulator import (
    MetricsAccumulator,
)


class DefaultFineTuningMethod(FineTuningMethod):
    meta = PluginMeta(
        name="Default Fine Tuning Method",
        version="0.0.1",
        description="A default fine-tuning plugin",
    )

    def __init__(self):
        # uncomment and pass your credentials to use your own s3 bucket
        # creds = {
        #     "role_arn": "arn:aws:iam::123456789012:role/some_data"
        #     "aws_access_key_id": "TESTACCESSKEIDTEST11",
        #     "aws_secret_access_key": "QWERTY1232qdsadfasfg5349BBdf30ekp23odk03",
        # }
        # self.data_loader = AWSS3DataLoader(**creds)

        # with empty creds, use anonymous session
        creds = {
        }
        self.data_loader = AWSS3DataLoader(**creds)

        self.retriever = TextQueryRetriever()
        self.parser = AWSS3ClickstreamParser(
            TextQueryItem, SearchResult, DummyEventType
        )
        self.splitter = ClickstreamSessionsSplitter()
        self.normalizer = DatasetFieldsNormalizer("item", "item_id")
        self.storage_producer = CLIPItemStorageProducer(self.normalizer)

        self.accumulators = [
            MetricsAccumulator("train_loss", True, True, True, True),
            MetricsAccumulator(
                "train_not_irrelevant_dist_shift", True, True, True, True
            ),
            MetricsAccumulator(
                "train_irrelevant_dist_shift", True, True, True, True
            ),
            MetricsAccumulator("test_loss"),
            MetricsAccumulator("test_not_irrelevant_dist_shift"),
            MetricsAccumulator("test_irrelevant_dist_shift"),
        ]

        self.manager = ExperimentsManager(
            tracking_uri=settings.MLFLOW_TRACKING_URI,
            main_metric="test_not_irrelevant_dist_shift",
            accumulators=self.accumulators,
        )

        self.initial_params = INITIAL_PARAMS
        self.initial_params.update(
            {
                "not_irrelevant_only": [True],
                "negative_downsampling": [
                    0.5,
                ],
                "examples_order": [
                    [
                        11,
                    ]
                ],
            }
        )

        self.settings = FineTuningSettings(
            loss_func=CosineProbMarginRankingLoss(),
            step_size=35,
            test_each_n_sessions=0.5,
            num_epochs=3,
        )

    def upload_initial_model(self) -> None:
        model = TextToImageCLIPModel(SentenceTransformer("clip-ViT-B-32"))
        self.manager.upload_initial_model(model)

    def get_fine_tuning_builder(
        self, clickstream: List[SessionWithEvents]
    ) -> FineTuningBuilder:
        ranking_dataset = prepare_data(
            clickstream,
            self.parser,
            self.splitter,
            self.retriever,
            self.data_loader,
            self.storage_producer,
        )
        fine_tuning_builder = FineTuningBuilder(
            data_loader=self.data_loader,
            query_retriever=self.retriever,
            clickstream_parser=self.parser,
            clickstream_sessions_splitter=self.splitter,
            dataset_fields_normalizer=self.normalizer,
            item_storage_producer=self.storage_producer,
            accumulators=self.accumulators,
            experiments_manager=self.manager,
            fine_tuning_settings=self.settings,
            initial_params=self.initial_params,
            ranking_data=ranking_dataset,
            initial_max_evals=5,
        )
        return fine_tuning_builder
