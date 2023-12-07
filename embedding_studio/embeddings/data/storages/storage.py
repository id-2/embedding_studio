from typing import Any, List, Optional

from datasets import Dataset


class ItemsStorage(Dataset):
    def __init__(
        self, dataset: Dataset, item_field_name: str, id_field_name: str
    ):
        """Dataset wrapper to represent storage of search result items

        :param dataset: items huggingface like dataset
        :type dataset: Dataset
        :param item_field_name: field represents item to be passed to embedding model
        :type item_field_name: str
        :param id_field_name: ID of item
        :type id_field_name: str
        """
        super(ItemsStorage, self).__init__(
            arrow_table=dataset._data,
            info=dataset._info,
            split=dataset._split,
            indices_table=dataset._indices,
            fingerprint=dataset._fingerprint,
        )
        self.item_field_name = item_field_name
        self.id_field_name = id_field_name

        self.id_to_index = {
            row[self.id_field_name]: index for index, row in enumerate(dataset)
        }

    def rows_by_ids(self, ids: List[Any]) -> dict:
        """Get rows by row ids

        :param ids:
        :type ids: List[Any]
        :return: rows from original dataset
        """
        return self[[self.id_to_index[id_] for id_ in ids]]

    def items_by_indices(self, indices: List[int]) -> List[Any]:
        """
        Get a slice of items from the dataset based on a list of indices.

        :param indices: List of indices to retrieve items.
        :type indices: list[int]
        :return: List of items corresponding to the given indices.
        :rtype: list
        """
        return self[indices][self.item_field_name]

    def items_by_ids(self, ids: List[Any]) -> List[Any]:
        """
        Get a slice of items from the dataset based on a list of ids.

        :param indices: List of indices to retrieve items.
        :type indices: list[int]
        :return: List of items corresponding to the given indices.
        :rtype: list
        """
        return self.rows_by_ids(ids)[self.item_field_name]

    def items_slice(
        self, start_idx: int = 0, end_idx: Optional[int] = None
    ) -> List[Any]:
        """Get a slice of items from the dataset based on a range of indices.

        :param start_idx: Start index of the slice.
        :type start_idx: int
        :param end_idx: End index of the slice (exclusive).
        :type end_idx: Optional[int]
        :return: List of items within the specified range of indices.
        :rtype: list
        """
        end_idx: int = end_idx if end_idx is not None else len(self)
        return self[start_idx:end_idx][self.item_field_name]