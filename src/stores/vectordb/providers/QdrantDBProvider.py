from  qdrant_client import models, QdrantClient
from ..VectorDBEInterface import VectorDBInterface
from ..VectorDBEnums import DistanceMethodEnums
import logging
from typing import List
from ....models.db_schemes import RetrievedDocument
class QdrantDBProvider(VectorDBInterface):

    def __init__(self, db_path: str, distance_method: str):

        self.client = None
        self.db_path = db_path
        self.distance_method = None

        if distance_method == DistanceMethodEnums.COSINE.value:
            self.distance_method = models.Distance.COSINE
        elif distance_method == DistanceMethodEnums.DOT.value:
            self.distance_method = models.Distance.DOT

        self.logger = logging.getLogger(__name__)

    def connect(self):
        self.client = QdrantClient(path=self.db_path)

    def disconnect(self):
        self.client = None

    def is_collection_existed(self, collection_name: str) -> bool:
        return self.client.collection_exists(collection_name=collection_name)
    
    def list_all_collections(self) -> List:
        return self.client.get_collections()
    
    def get_collection_info(self, collection_name: str) -> dict:
        return self.client.get_collection(collection_name=collection_name)
    
    def delete_collection(self, collection_name: str):
        if self.is_collection_existed(collection_name):
            return self.client.delete_collection(collection_name=collection_name)
        
    def create_collection(self, collection_name: str, 
                                embedding_size: int,
                                do_reset: bool = False):
        if do_reset:
            _ = self.delete_collection(collection_name=collection_name)
        
        if not self.is_collection_existed(collection_name):
             # START of changes
            _ = self.client.create_collection(
                collection_name=collection_name,
                vectors_config={
                    # Dense vectors for semantic search
                    "dense": models.VectorParams(
                        size=embedding_size,
                        distance=self.distance_method
                    ),
                },
                # Sparse vectors for keyword search
                sparse_vectors_config={
                   "sparse": models.SparseVectorParams(
                       index=models.SparseIndexParams(
                           on_disk=False,
                       )
                   )
                }
            )
            # END of changes

            return True
        
        return False
    
    def insert_one(self, collection_name: str, text: str, vector: list,
                         metadata: dict = None, 
                         record_id: str = None):
        
        if not self.is_collection_existed(collection_name):
            self.logger.error(f"Can not insert new record to non-existed collection: {collection_name}")
            return False
        
        try:
            _ = self.client.upload_records(
                collection_name=collection_name,
                records=[
                    models.Record(
                        id=[record_id],
                        vector=vector,
                        payload={
                            "text": text, "metadata": metadata
                        }
                    )
                ]
            )
        except Exception as e:
            self.logger.error(f"Error while inserting batch: {e}")
            return False

        return True
    
    def insert_many(self, collection_name: str, texts: list, 
                          dense_vectors: list, sparse_vectors: list, # Modified parameters
                          metadata: list = None, 
                          record_ids: list = None, batch_size: int = 50):
        
        if metadata is None:
            metadata = [None] * len(texts)

        if record_ids is None:
            record_ids = list(range(0, len(texts)))

        for i in range(0, len(texts), batch_size):
            batch_end = i + batch_size

            batch_texts = texts[i:batch_end]
            batch_dense_vectors = dense_vectors[i:batch_end]
            batch_sparse_vectors = sparse_vectors[i:batch_end]
            batch_metadata = metadata[i:batch_end]
            batch_record_ids = record_ids[i:batch_end]

            batch_records = [
                models.Record(
                    id=batch_record_ids[x],
                    vector={
                        "dense": batch_dense_vectors[x],
                        "sparse": models.SparseVector(**batch_sparse_vectors[x])
                    },
                    payload={
                        "text": batch_texts[x], "metadata": batch_metadata[x]
                    }
                )
                for x in range(len(batch_texts))
            ]

            try:
                _ = self.client.upload_records(
                    collection_name=collection_name,
                    records=batch_records,
                )
            except Exception as e:
                self.logger.error(f"Error while inserting batch: {e}")
                return False

        return True
        
    def search_by_vector(self, collection_name: str, vector: list, limit: int = 5):
        
        # Use the modern query_points API for simple dense search
        results = self.client.query_points(
            collection_name=collection_name,
            query=vector,  # For simple search, the vector goes directly into 'query'
            using="dense", # Specify which named vector to use
            limit=limit
        )

        if not results or not hasattr(results, 'points') or len(results.points) == 0:
            return None
        
        return [
            RetrievedDocument(**{
                "score": result.score,
                "text": result.payload["text"],
            })
            for result in results.points
        ]
    

    def search_hybrid(self, collection_name: str, dense_vector: list, sparse_vector: dict,
                      dense_limit: int, sparse_limit: int, limit: int):
        
        # Define the two searches we want to run in parallel
        prefetches = [
            models.Prefetch(
                query=dense_vector,
                using="dense",
                limit=dense_limit,
            ),
            models.Prefetch(
                query=models.SparseVector(**sparse_vector),
                using="sparse",
                limit=sparse_limit
            )
        ]

        # Use Reciprocal Rank Fusion (RRF) to combine the results
        results = self.client.query_points(
            collection_name=collection_name,
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            prefetch=prefetches,
            limit=limit
        )

        if not results or len(results.points) == 0:
            return None
        
        return [
            RetrievedDocument(**{
                "score": result.score,
                "text": result.payload["text"],
            })
            for result in results.points
        ]