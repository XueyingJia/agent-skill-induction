import json

def load_and_concatenate_workflows(json_path):
    """
    Load retrieved_workflows from the JSON file and concatenate the workflow content
    
    Args:
        json_path: Path to the retrieval_result JSON file
        
    Returns:
        A string containing all concatenated workflows
    """
    try:
        # Load the JSON file
        with open(json_path, 'r') as f:
            data = json.load(f)
        
        # Extract the workflows
        workflows = data.get("retrieved_workflows", [])
        
        # Concatenate the workflow texts
        concatenated_workflows = "Here are some relevant workflows for your reference:\n\n"
        for workflow in workflows:
            workflow_text = workflow.get("workflow", "")
            
            concatenated_workflows += f"{workflow_text}\n\n"
        
        return concatenated_workflows
            
    except Exception as e:
        return f"Error loading workflows: {str(e)}"

# Example usage
# json_path = "/Users/xueyingjia/Documents/GitHub/agent-skill-induction/asi/retrieval_outputs/retrieval_result_1.json"
# concatenated_workflows = load_and_concatenate_workflows(json_path)
# print(concatenated_workflows)