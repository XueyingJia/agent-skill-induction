from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings
import json
from langchain.docstore.document import Document
import tiktoken
import os
import time
import argparse
import shutil

def count_tokens(text):
    encoding = tiktoken.encoding_for_model("text-embedding-3-small")
    return len(encoding.encode(text))

embeddings = OpenAIEmbeddings(
    model=os.getenv("EMBEDDING_MODEL"),
    openai_api_key=os.getenv("LITELLM_API_KEY"),
    openai_api_base=os.getenv("LITELLM_BASE_URL")
)

def save_results_to_file(results_data, query_tokens_used, retrieved_ids, file_name="retrieval_result.json"):
    output_data = {
        "retrieved_workflows": results_data['retrieved_workflows'],
        "query_tokens_used": query_tokens_used,
        "retrieved_ids": retrieved_ids
    }
    with open(file_name, 'w') as f:
        json.dump(output_data, f, indent=4)
    print(f"Results saved to {file_name}")

def retrieve_workflow(task_id, task_type, natural_language_description, use_all=False, use_top_k=False, top_k=3, use_distance=False, distance_threshold=1.0):
    # help me print out all the parameters for double checking
    print(f"Task ID: {task_id}")
    print(f"Task Type: {task_type}")
    print(f"Natural Language Description: {natural_language_description}")
    print(f"Use All: {use_all}")
    print(f"Use Top K: {use_top_k}")
    print(f"Top K: {top_k}")
    print(f"Use Distance: {use_distance}")
    print(f"Distance Threshold: {distance_threshold}")

    start_time = time.time()

    # Initialize token counter
    query_tokens_used = 0
    retrieved_ids = []
    results_data = {"retrieved_workflows": []}

    # Initialize or populate the vector store
    vectorstore = Chroma(
        persist_directory=f"chroma_db_{task_type}",
        embedding_function=embeddings
    )
    collection_data = vectorstore.get()
    if not collection_data.get("ids", []):
        save_results_to_file(results_data, query_tokens_used, retrieved_ids, file_name=f"retrieval_outputs/retrieval_result_{task_id}.json")
        return '', 0, []

    # Create a retriever or retrieve all documents
    if use_all:
        all_data = vectorstore.get()
        documents = all_data.get('documents', [])
        metadatas = all_data.get('metadatas', [])
        res = '\n'.join(doc for doc in documents)
        for doc, metadata in zip(documents, metadatas):
            results_data["retrieved_workflows"].append({"id": metadata["id"], "workflow": doc, 'score': None})
        save_results_to_file(results_data, query_tokens_used, retrieved_ids, file_name=f"retrieval_outputs/retrieval_result_{task_id}.json")
        return res, query_tokens_used, retrieved_ids

    retriever = vectorstore.as_retriever()
    query_tokens_used = count_tokens(natural_language_description)
    
    # update query tokens used in chroma.json
    metadata_file = "chroma.json"
    if os.path.exists(metadata_file):
        with open(metadata_file, 'r') as f:
            stats = json.load(f)
    else:
        # Initialize metadata file if it doesn't exist
        stats = {"vectordb_stats": {}}
    # Update stats for this task_type
    if task_type not in stats["vectordb_stats"]:
        stats["vectordb_stats"][task_type] = {
            "total_workflows": 0,
            "total_tokens_used": 0,
            "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            'query_tokens_used': query_tokens_used,
        }
    else:      
        stats["vectordb_stats"][task_type]["query_tokens_used"] += query_tokens_used
        stats["vectordb_stats"][task_type]["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(metadata_file, 'w') as f:
        json.dump(stats, f, indent=4, ensure_ascii=False)

    # Perform retrieval
    vector_data = vectorstore.get()
    number_of_workflows = len(vector_data.get('ids', []))
    print(f"in total {len(vector_data.get('ids', []))} workflows in the vector store")
    results = vectorstore.similarity_search_with_score(natural_language_description, k=number_of_workflows)
    results = sorted(results, key=lambda x: x[1])  # Sort by score (ascending)

    filtered_results = []
    if use_top_k:
        results = results[:top_k]
    elif use_distance:
        results = [r for r in results if r[1] < distance_threshold]

    for doc, score in results:
        filtered_results.append(doc)
        retrieved_ids.append((doc.metadata['id'], score))
        results_data["retrieved_workflows"].append({
            "id": doc.metadata["id"],
            "workflow": doc.page_content,
            "score": score
        })

    # Combine results into a single string
    res = '\n'.join(doc.page_content for doc in filtered_results)

    # Save results to a JSON file
    save_results_to_file(results_data, query_tokens_used, retrieved_ids, file_name=f"retrieval_outputs/retrieval_result_{task_id}.json")

    print(f"Retrieval completed in {time.time() - start_time:.2f} seconds.")
    return res, query_tokens_used, retrieved_ids

def visualize_vectorstore_data(task_type):

    vectorstore = Chroma(
        persist_directory=f"chroma_db_{task_type}",
        embedding_function=embeddings
    )
    # Ensure the vector store is populated
    collection_data = vectorstore.get()
    if not collection_data.get("ids", []):
        print("The vector store is empty.")
        return

    # Retrieve all documents
    all_data = vectorstore.get()
    documents = all_data.get('documents', [])
    metadatas = all_data.get('metadatas', [])

    # Print the documents
    for doc, metadata in zip(documents, metadatas):
        print(f"id: {metadata['id']}")
        print(f"workflow: {doc[:100]}")
        print("---"*10)

def reset_db(task_type):
    db_path = f"chroma_db_{task_type}"
    if os.path.exists(db_path):
        shutil.rmtree(db_path)
    vectorstore = Chroma(
        persist_directory=f"chroma_db_{task_type}",
        embedding_function=embeddings
    )
    vectorstore.reset_collection()

def delete_workflow_by_id(task_type, workflow_id):
    """
    Delete a workflow with a specific ID from the vector store.
    
    Args:
        task_type: Type of the task, determines the database path
        workflow_id: ID of the workflow to delete
    
    Returns:
        bool: True if deleted successfully, False if the ID wasn't found
    """
    try:
        # Initialize the vector store
        vectorstore = Chroma(
            persist_directory=f"chroma_db_{task_type}",
            embedding_function=embeddings
        )
        
        # Get all documents
        collection_data = vectorstore.get()
        documents = collection_data.get("documents", [])
        metadatas = collection_data.get("metadatas", [])
        ids = collection_data.get("ids", [])
        
        # Find the internal Chroma ID for the workflow ID
        target_ids = []
        for i, metadata in enumerate(metadatas):
            if metadata.get("id") == workflow_id:
                target_ids.append(ids[i])
                
        if not target_ids:
            print(f"No workflow found with ID: {workflow_id}")
            return False
            
        # Delete the documents by their internal IDs
        vectorstore.delete(ids=target_ids)
        print(f"Successfully deleted workflow with ID: {workflow_id}")
        return True
    
    except Exception as e:
        print(f"Error deleting workflow: {e}")
        return False
    
def add_workflow_to_db(task_type, workflow_data):
    """
    Add a single workflow to the vector store.
    
    Args:
        task_type: Type of the task, determines the database path
        workflow_data: Dictionary containing 'id', 'task', and 'workflow_lines' keys
        
    Returns:
        bool: True if added successfully, False if an error occurred
    """
    try:
        # Validate workflow_data structure
        required_keys = ['id', 'task', 'workflow_lines']
        for key in required_keys:
            if key not in workflow_data:
                print(f"Error: Missing required key '{key}' in workflow_data")
                return False
                
        # Initialize the vector store
        vectorstore = Chroma(
            persist_directory=f"chroma_db_{task_type}",
            embedding_function=embeddings
        )
        
        # Create document
        tokens_used = count_tokens('\n'.join(workflow_data['workflow_lines']))
        document = Document(
            page_content='\n'.join(workflow_data['workflow_lines']),
            metadata={
                "id": workflow_data['id'],
                "task": workflow_data['task'],
                "tokens_used": tokens_used,
            }
        )
                
        # Add document to vector store
        vectorstore.add_documents([document])
        print(f"Successfully added workflow with ID: {workflow_data['id']}")
        
        # Verify addition
        collection_data = vectorstore.get()
        print(f"Vector store now has {len(collection_data.get('ids', []))} documents")

        # Update simple metadata file
        metadata_file = "chroma.json"
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        
        if os.path.exists(metadata_file):
            with open(metadata_file, 'r') as f:
                stats = json.load(f)
        else:
            # Initialize metadata file if it doesn't exist
            stats = {"vectordb_stats": {}}
            
        # Update stats for this task_type
        if task_type not in stats["vectordb_stats"]:
            stats["vectordb_stats"][task_type] = {
                "total_workflows": 1,
                "total_tokens_used": tokens_used,
                "last_updated": now,
                'qury_tokens_used': 0,
            }
        else:
            # Check if we're updating an existing workflow or adding a new one
            collection_data = vectorstore.get()
            stats["vectordb_stats"][task_type]["total_workflows"] += 1
            stats["vectordb_stats"][task_type]["total_tokens_used"] += tokens_used
            stats["vectordb_stats"][task_type]["last_updated"] = now
        
        return True
        
    except Exception as e:
        print(f"Error adding workflow: {e}")
        return False

def load_workflows_from_db(task_type):
    """
    Load all workflows from the vector database for a specific task type.
    
    Args:
        task_type: Type of the task, determines the database path
        
    Returns:
        list: A list of dictionaries containing 'id', 'task', and 'content' for each workflow
    """
    try:
        # Initialize the vector store
        vectorstore = Chroma(
            persist_directory=f"chroma_db_{task_type}",
            embedding_function=embeddings
        )
        
        # Get all documents
        collection_data = vectorstore.get()
        if not collection_data.get("ids", []):
            print(f"No workflows found for task type: {task_type}")
            return []
            
        documents = collection_data.get("documents", [])
        metadatas = collection_data.get("metadatas", [])
        
        # Extract the required fields
        workflows = []
        for doc, metadata in zip(documents, metadatas):
            workflows.append({
                "id": metadata.get("id"),
                "task": metadata.get("task"),
                "content": doc
            })
            
        print(f"Loaded {len(workflows)} workflows from {task_type} database")
        return workflows
        
    except Exception as e:
        print(f"Error loading workflows from database: {e}")
        return []
    
# reset the collection
# task_type = "all"
# reset_db(task_type)

# test query the vector store
# res, query_tokens_used, retrieved_ids = retrieve_workflow('1', 'all', "Compare the time for walking and driving route from 5000 Fifth Avenue, Pittsburgh to UPMC family health center", use_distance=True, distance_threshold=10.0)
# print("query_tokens_used:", query_tokens_used)
# print("Retrieved IDs:", retrieved_ids)
# print("Result:", res)

# # visualize the vector store data
# visualize_vectorstore_data("all")

# # delete a workflow by ID
# delete_workflow_by_id("all", 0)
# visualize_vectorstore_data("all")



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Retrieve workflows from the vector store.")
    parser.add_argument("--task_id", required=True, help="Task ID")
    parser.add_argument("--task_type", required=True, help="Task type")
    parser.add_argument("--natural_language_description", required=True, help="Natural language description of the task")
    parser.add_argument("--top_k", type=int, default=3, help="Number of top-k results to retrieve")
    parser.add_argument("--use_all", action="store_true", help="Retrieve all workflows")
    parser.add_argument("--use_top_k", action="store_true", help="Use top-k retrieval")
    parser.add_argument("--use_distance", action="store_true", help="Use distance-based retrieval")
    parser.add_argument("--distance_threshold", type=float, default=1.0, help="Distance threshold for retrieval")

    args = parser.parse_args()

    retrieve_workflow(
        task_id=args.task_id,
        task_type=args.task_type,
        natural_language_description=args.natural_language_description,
        use_all=args.use_all,
        use_top_k=args.use_top_k,
        top_k=args.top_k,
        use_distance=args.use_distance,
        distance_threshold=args.distance_threshold
    )