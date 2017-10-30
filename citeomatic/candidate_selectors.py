from abc import ABC

from citeomatic.neighbors import EmbeddingModel, ANN
from citeomatic.corpus import Corpus
from whoosh import scoring, qparser

from citeomatic.neighbors import ANN
from whoosh.qparser import QueryParser, MultifieldParser
from citeomatic.common import schema, FieldNames
from whoosh.index import open_dir
import logging


class CandidateSelector(ABC):
    def __init__(self, top_k=100):
        self.top_k = top_k

    def fetch_candidates(self, doc_id, candidates_id_pool) -> list:
        """
        For each query paper, return a list of candidates and associated scores
        :param doc_id: Document ID to get candidates for
        :param top_k: How many top candidates to fetch
        :param candidates_id_pool: Set of candidate IDs to limit candidates to
        :return:
        """
        pass


class ANNCandidateSelector(CandidateSelector):
    def __init__(
            self,
            corpus: Corpus,
            ann: ANN,
            paper_embedding_model: EmbeddingModel,
            top_k: int,
            extend_candidate_citations: bool
    ):
        super().__init__(top_k)
        self.corpus = corpus
        self.ann = ann
        self.paper_embedding_model = paper_embedding_model
        self.extend_candidate_citations = extend_candidate_citations

    def fetch_candidates(self, doc_id, candidate_ids_pool):
        doc = self.corpus[doc_id]
        doc_embedding = self.paper_embedding_model.embed(doc)
        # 1. Fetch candidates from ANN index
        nn_candidates = self.ann.get_nns_by_vector(doc_embedding, self.top_k + 1)
        # 2. Remove the current document from candidate list
        if doc_id in nn_candidates:
            nn_candidates.remove(doc_id)
        candidate_ids = nn_candidates[:self.top_k]

        # 3. Check if we need to include citations of candidates found so far.
        if self.extend_candidate_citations:
            extended_candidate_ids = []
            for candidate_id in candidate_ids:
                extended_candidate_ids.extend(self.corpus[candidate_id].out_citations)
            candidate_ids = candidate_ids + extended_candidate_ids
        logging.debug("Number of candidates found: {}".format(len(candidate_ids)))
        candidate_ids_pool = set(candidate_ids_pool)
        candidate_ids = set(candidate_ids).intersection(candidate_ids_pool)
        if doc_id in candidate_ids:
            candidate_ids.remove(doc_id)
        return list(candidate_ids)


class BM25CandidateSelector(CandidateSelector):
    def __init__(
            self,
            corpus: Corpus,
            index_path: str,
            top_k,
            extend_candidate_citations: bool
    ):
        super().__init__(top_k)
        self.index_path = index_path
        self._bm25_index = open_dir(self.index_path, schema=schema)
        self.searcher = self._bm25_index.searcher(weighting=scoring.BM25F)
        # TODO (chandra): Think about how to tune this query so the baseline is stronger. Currently
        # we just search for words in the title of the query document in the title and abstract
        # fields of candidate documents.
        self.query_parser = MultifieldParser([FieldNames.TITLE, FieldNames.ABSTRACT],
                                             self._bm25_index.schema, group=qparser.OrGroup)
        self.corpus = corpus
        self.extend_candidate_citations = extend_candidate_citations

    def fetch_candidates(self, doc_id, candidate_ids_pool):
        query_text = self.corpus[doc_id].title
        # Implement BM25 index builder and return
        query = self.query_parser.parse(query_text)
        results = self.searcher.search(query, limit=self.top_k + 1)

        candidate_ids = [h['id'] for h in results][:self.top_k]

        if self.extend_candidate_citations:
            extended_candidate_ids = []
            for candidate_id in candidate_ids:
                extended_candidate_ids.extend(self.corpus[candidate_id].out_citations)
            candidate_ids = candidate_ids + extended_candidate_ids

        candidate_ids_pool = set(candidate_ids_pool)
        candidate_ids = [c_id for c_id in candidate_ids if c_id in candidate_ids_pool and c_id != doc_id]

        return candidate_ids