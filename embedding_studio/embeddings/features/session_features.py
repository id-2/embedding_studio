from typing import Optional

import torch
from torch import FloatTensor, Tensor


class SessionFeatures:
    def __init__(
        self,
        positive_ranks: Optional[FloatTensor] = None,
        negative_ranks: Optional[FloatTensor] = None,
        target: Optional[Tensor] = None,
        positive_confidences: Optional[FloatTensor] = None,
        negative_confidences: Optional[FloatTensor] = None,
    ):
        """Extracted features of clickstream session using embeddings.

        :param positive_ranks: ranks of positive results
        :type positive_ranks: Optional[FloatTensor]
        :param negative_ranks: ranks of negative results
        :type negative_ranks: Optional[FloatTensor]
        :param target: if target == 1 ranks are similarities, if target == -1 ranks are distances
        :type target: Optional[Tensor]
        :param positive_confidences: confidences of positive results (like clicks)
        :type positive_confidences: Optional[FloatTensor]
        :param negative_confidences: confidences of not positive results
        :type negative_confidences: Optional[FloatTensor]
        """

        self._positive_ranks = positive_ranks
        self._negative_ranks = negative_ranks
        self._target = target
        self._positive_confidences = positive_confidences
        self._negative_confidences = negative_confidences
        self._check_types()
        self._check_lengths()

    def _check_types(self):
        if self.positive_ranks is not None and not isinstance(
            self.positive_ranks, torch.Tensor
        ):
            raise TypeError("positive_ranks must be a torch.Tensor or None")
        if self.negative_ranks is not None and not isinstance(
            self.negative_ranks, torch.Tensor
        ):
            raise TypeError("negative_ranks must be a torch.Tensor or None")
        if self.target is not None and not isinstance(
            self.target, torch.Tensor
        ):
            raise TypeError("target must be a torch.Tensor or None")
        if self.positive_confidences is not None and not isinstance(
            self.positive_confidences, torch.Tensor
        ):
            raise TypeError(
                "positive_confidences must be a torch.Tensor or None"
            )
        if self.negative_confidences is not None and not isinstance(
            self.negative_confidences, torch.Tensor
        ):
            raise TypeError(
                "negative_confidences must be a torch.Tensor or None"
            )

    def _check_lengths(self):
        length_set = {
            len(x)
            for x in [
                self.positive_ranks,
                self.negative_ranks,
                self.target,
                self.positive_confidences,
                self.negative_confidences,
            ]
            if x is not None
        }
        if len(length_set) > 1:
            raise ValueError(
                "All non-None parameters must have the same length"
            )

    @property
    def positive_ranks(self) -> Optional[FloatTensor]:
        return self._positive_ranks

    @positive_ranks.setter
    def positive_ranks(self, value: Optional[FloatTensor]):
        self._positive_ranks = value
        self._check_types()
        self._check_lengths()

    @property
    def negative_ranks(self) -> Optional[FloatTensor]:
        return self._negative_ranks

    @negative_ranks.setter
    def negative_ranks(self, value: Optional[FloatTensor]):
        self._negative_ranks = value
        self._check_types()
        self._check_lengths()

    @property
    def target(self) -> Optional[Tensor]:
        return self._target

    @target.setter
    def target(self, value: Optional[Tensor]):
        self._target = value
        self._check_types()
        self._check_lengths()

    @property
    def positive_confidences(self) -> Optional[FloatTensor]:
        return self._positive_confidences

    @positive_confidences.setter
    def positive_confidences(self, value: Optional[FloatTensor]):
        self._positive_confidences = value
        self._check_types()
        self._check_lengths()

    @property
    def negative_confidences(self) -> Optional[FloatTensor]:
        return self._negative_confidences

    @negative_confidences.setter
    def negative_confidences(self, value: Optional[FloatTensor]):
        self._negative_confidences = value
        self._check_types()
        self._check_lengths()

    def _accumulate(self_var: Tensor, other_var: Tensor):
        if self_var is not None and other_var is not None:
            return torch.cat([self_var, other_var])
        elif other_var is not None:
            return other_var

    def __iadd__(self, other):
        """Accumulate features from another session

        :param other: other session
        :type other: SessionFeatures
        :return: aggregates features
        :rtype: SessionFeatures
        """

        self._positive_ranks = SessionFeatures._accumulate(
            self._positive_ranks, other._positive_ranks
        )
        self._negative_ranks = SessionFeatures._accumulate(
            self._negative_ranks, other._negative_ranks
        )
        self._target = SessionFeatures._accumulate(self._target, other._target)
        self._positive_confidences = SessionFeatures._accumulate(
            self._positive_confidences, other._positive_confidences
        )
        self._negative_confidences = SessionFeatures._accumulate(
            self._negative_confidences, other._negative_confidences
        )

        self._check_types()
        self._check_lengths()
        return self

    def clamp_diff_in(self, min: float, max: float):
        """Filter min < |positive_ranks - negative_ranks| < max examples.

        :param min: minimal difference between pos and neg ranks
        :type min: float
        :param max: maximal difference between pos and neg ranks
        :type max: float
        """
        if (
            self._positive_ranks is not None
            and self._negative_ranks is not None
        ):
            hard_examples: Tensor = (
                torch.abs(self._positive_ranks - self._negative_ranks) > min
            )
            smooth_examples: Tensor = (
                torch.abs(self._positive_ranks - self._negative_ranks) < max
            )
            examples: Tensor = torch.logical_and(
                hard_examples, smooth_examples
            )

            self._positive_ranks = self._positive_ranks[examples]
            self._negative_ranks = self._negative_ranks[examples]
            self._target = self._target[examples]
            self._positive_confidences = self._positive_confidences[examples]
            self._negative_confidences = self._negative_confidences[examples]
            self._check_lengths()

    def use_positive_from(self, other):
        """If session is fully irrelevant, to use positive pairs from another session.
        This way "triple loss" becomes "contrastive"

        :param other: not irrelevant session with positive evidences
        :type other: SessionFeatures
        """
        other._check_types()
        other._check_lengths()

        if self._negative_ranks.shape[0] < other._positive_ranks.shape[0]:
            positive_ranks_: FloatTensor = other._positive_ranks[
                : self._negative_ranks.shape[0]
            ]
        elif self._negative_ranks.shape[0] > other._positive_ranks.shape[0]:
            self._negative_ranks = self._negative_ranks[
                : other._positive_ranks.shape[0]
            ]
            self._target = self._target[: other._positive_ranks.shape[0]]
            self._negative_confidences = self._negative_confidences[
                : other._positive_ranks.shape[0]
            ]
            positive_ranks_: FloatTensor = other._positive_ranks
        else:
            positive_ranks_: FloatTensor = other._positive_ranks

        self._positive_confidences = other._positive_ranks
        self._positive_ranks = positive_ranks_

        self._check_types()
        self._check_lengths()
