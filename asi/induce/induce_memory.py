import os
import json
import litellm
import argparse
from induce.utils import get_output_dir, get_task_id
import sys
# Add the parent directory to sys.path to allow importing from asi
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Now import functions from chroma_retriever.py
from chroma_retriever import add_workflow_to_db, delete_workflow_by_id, load_workflows_from_db


# %% Induce Memory

def get_example_query_cleaned(index: int, result_dir: str, config_dir: str) -> str:
    """Get the string for a past example experience, without invalid actions."""
    # get config
    cid = get_task_id(result_dir)
    config_path = os.path.join(config_dir, f"{cid}.json")
    config = json.load(open(config_path))

    # get instruction
    subtask_inst_path = os.path.join(result_dir, "instruction.txt")
    if os.path.exists(subtask_inst_path):  # sub task
        instruction = open(subtask_inst_path, 'r').read()
        task = instruction
    else:  # full task
        instruction = config["intent"]
        task = config["intent_template"]

    steps = json.load(open(os.path.join(result_dir, "cleaned_steps.json")))
    if len(steps) > 0:
        print(f"Collected #{len(steps)} valid steps.")
        steps = '\n'.join(steps)
        ex = f"### Example {index} ({config['task_id']}): {instruction}\n{steps}"
    else:
        ex = None

    return ex, task
    

def get_test_query(result_dir_list: str, config_dir: str) -> str | None:
    """Transform each result log into an input experience and form a query."""
    task = None
    examples = []
    for rdir in result_dir_list:  # e.g., ['results/webarena.110_2_0', 'results/webarena.111_2_1']
        ex, task = get_example_query_cleaned(len(examples)+1, rdir, config_dir)
        if ex is None: continue
        examples.append(ex)
    
    if len(examples) < 1:
        return None
    query = f"## Task: {task}\n" + '\n\n'.join(examples)
    return query

def store_token_usage(response: litellm.Response, type: str) -> None:
    print(f"Storing token usage for type: {type}")
    usage_data = response.usage
    print(f"Token usage: {usage_data}")
    induce_token_tracker = 'induce_token_tracker'
    usage_file_path = os.path.join(induce_token_tracker, f"{args.website}.json")
    print(f"Usage file path: {usage_file_path}")
    ids = [file.split('.')[-1] for file in args.result_id_list]
    print(f"IDs: {ids}")
    token_type = f"{type}_token"
    if os.path.exists(usage_file_path):
        try:
            with open(usage_file_path, 'r') as usage_file:
                existing_data = json.load(usage_file)
            existing_data[token_type]["total_token"] += usage_data["total_tokens"]
            existing_data[token_type]["prompt_token"] += usage_data["prompt_tokens"]
            existing_data[token_type]["completion_token"] += usage_data["completion_tokens"]
            existing_data[f'{type}_workflow_id'].append(ids)
        except Exception as e:
            raise ValueError(f"Error reading or updating JSON file: {e}")
    with open(usage_file_path, 'w') as usage_file:
            json.dump(existing_data, usage_file, indent=4)
    print(f"Usage data saved to {usage_file_path}")


def induce_workflows() -> list[str]:
    result_dir_list = args.result_id_list
    # result_dir_list = get_result_dirs(args.results_dir, args.result_id_list, args.template_id, args.config_dir)
    test_query = get_test_query(result_dir_list, args.config_dir)
    if test_query is None: return []
    with open(args.test_query_path, 'w') as fw:
        fw.write(test_query)
    
    # load existing workflows
    existing_workflows = '\n'.join([workflow['content'] for workflow in load_workflows_from_db()])

    messages = [{"role": "system", "content": open(args.sys_msg_path).read()}]
    messages += [{"role": "user", "content": open(args.instruction_path).read()}]
    messages += [{"role": "user", "content": open(args.few_shot_path).read()}]
    messages += [{"role": "user", "content": "## Existing Workflows\n" + existing_workflows}]
    messages += [{"role": "user", "content": test_query + '\n\n## Reusable Workflows'}]

    all_responses = []
    if "openai" in args.model:
        response = litellm.completion(
            api_key=os.environ.get("LITELLM_API_KEY"),
            base_url=os.environ.get("LITELLM_BASE_URL", "https://cmu.litellm.ai"),
            model=args.model,
            messages=messages,
            temperature=args.temperature,
            n=args.num_responses,
        )
        store_token_usage(response, "induce")

        for i, resp in enumerate(response.choices):
            curr_resp = resp.message.content
            curr_path = os.path.join(args.output_dir, f"{i}.md")
            with open(curr_path, 'w') as fw:
                fw.write(test_query + '\n\n\n' + curr_resp)
            all_responses.append(curr_resp)
    else:
        for i in range(args.num_responses):
            response = litellm.completion(
                api_key=os.environ.get("LITELLM_API_KEY"),
                base_url=os.environ.get("LITELLM_BASE_URL", "https://cmu.litellm.ai"),
                model=args.model,
                messages=messages,
                temperature=args.temperature,
            )
            store_token_usage(response, "induce")

            curr_resp = response.choices[0].message.content
            curr_path = os.path.join(args.output_dir, f"{i}.md")
            with open(curr_path, 'w') as fw:
                fw.write(test_query + '\n\n\n' + curr_resp)
            all_responses.append(curr_resp)
    return all_responses

# %% Write Workflows
from induce.utils import extract_code_pieces

def get_workflow_name(workflow: str) -> str:
    """Get the name of the workflow."""
    name = workflow.split('\n')[0].lstrip("Task: ").strip()
    if 'Action Trajectory' in name:
        name = name.split('Action Trajectory')[0].strip()
    return name

def update_workflows(workflow: str, existing_workflows: list[str]) -> tuple[bool, list[str]]:
    """Update the existing workflows given the potentially topically similar new item.
    - If the new workflow does not overlap with any existing workflows, add it => True, []
    - If the new workflow topically overlap:
      - If the new workflow is better, replace the existing workflow => True, [existing_workflow_name]
        - If the existing workflow is better, keep the existing workflow => False, []
    """
    name = get_workflow_name(workflow)
    print(f"Workflow in update_workflows: {workflow}")
    print(f"Workflow name in update_workflows: {name}")
    print(f"Existing Workflows in update_workflows: {existing_workflows}")
    for ew in existing_workflows:
        ew_name = get_workflow_name(ew)
        messages = [
            {"role": "system", "content": "You are an expert in navigating the web, your task is to check if the two workflows refer to the same task."},
            {"role": "user", "content": "Does the following two workflows refer to the same task? Only return 'yes' or 'no', do not provide any additional information."},
            {"role": "user", "content": f"Workflow 1: {name}\nWorkflow 2: {ew_name}"}
        ]
        response = litellm.completion(
            api_key=os.environ.get("LITELLM_API_KEY"),
            base_url=os.environ.get("LITELLM_BASE_URL", "https://cmu.litellm.ai"),
            model=args.model,
            messages=messages,
            temperature=args.temperature,
        )
        store_token_usage(response, "update")
        response = response.choices[0].message.content
        
        if 'yes' in response: yes_index = response.index('yes')
        else: yes_index = 0
        if 'no' in response: no_index = response.index('no')
        else: no_index = len(response)
        if yes_index < no_index:
            print(f"Checking Overlap between [{name}] & [{ew_name}] => YES")
            # if the existing workflow is better, still count as overlap
            better_workflow = get_better_workflow(workflow, ew)
            action = "KEEP" if better_workflow == ew else "REPLACE"
            print(f"Better Workflow: {better_workflow} \n=> {action}")
            if better_workflow == ew: # existing workflow is better
                return False, []
            else:  # new workflow is better
                return True, [ew_name]
    print(f"Checking Overlap between [{name}] & [{len(existing_workflows)} Existing Workflows] => NO")
    return True, []

def get_better_workflow(workflow1: str, workflow2: str) -> str:
    """Select the better workflow between two topically-overlapping workflows."""
    messages = [
        {"role": "system", "content": "You are an expert in navigating the web, your task is to select the better navigation guidance workflow between the two workflows provided."},
        {"role": "user", "content": "Which workflow is more helpful in guiding web navigation? Only return 'Workflow 1' or 'Workflow 2', do not provide any additional information."},
        {"role": "user", "content": f"Workflow 1:\n{workflow1}\nWorkflow 2:\n{workflow2}"}
    ]
    response = litellm.completion(
        api_key=os.environ.get("LITELLM_API_KEY"),
        base_url=os.environ.get("LITELLM_BASE_URL", "https://cmu.litellm.ai"),
        model=args.model,
        messages=messages,
        temperature=args.temperature,
    )
    store_token_usage(response, "compare")
    response = response.choices[0].message.content
    if "workflow 1" in response.lower(): return workflow1
    elif "workflow 2" in response.lower(): return workflow2
    else: return None

def write_workflows(response: str) -> None:
    # get newly induced workflows
    workflows = extract_code_pieces(response, start='"""', end='"""', do_split=False)
    workflows = [w for w in workflows if ("Task" in w) and ("Action Trajectory" in w)]

    print(f"Induced workflows: {workflows}")

    # load existing workflows
    all_workflows = load_workflows_from_db()
    existing_workflows = []
    existing_workflow_names = []
    existing_workflow_name_to_id = {}
    max_id = 0
    for workflow_item in all_workflows:
        task = workflow_item.get("task", "")
        workflow_text = workflow_item.get("content", '')
        existing_workflows.append(workflow_text)
        existing_workflow_names.append(task)
        existing_workflow_name_to_id[task] = workflow_item.get("id", None)
        max_id = max(max_id, workflow_item.get("id", 0))
    
    print(f"Existing workflows: {existing_workflows}")
    print(f"Existing workflow names: {existing_workflow_names}")

    # update workflows
    for w in workflows:
        add_new, names_to_remove = update_workflows(w, existing_workflows)
        for name in names_to_remove:
            # delete the workflow from the database
            workflow_id = existing_workflow_name_to_id[name]
            if workflow_id is not None:
                delete_workflow_by_id(workflow_id)
                print(f"Deleted workflow with ID: {workflow_id}")
            else:
                print(f"Workflow name '{name}' not found in existing workflows.")
        if add_new: 
            # add the new workflow to the database
            add_workflow_to_db({
                'task': get_workflow_name(w),
                'workflow_lines': w.split('\n'),
                'id': max_id + 1
            }, task_type=args.website)
            max_id += 1
            print(f"Added new workflow with ID: {max_id}")


# %% Overall pipeline

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, default="claude", choices=["gpt-4o", "claude"])
    parser.add_argument("--num_responses", type=int, default=1, help="Number of responses to generate.")
    parser.add_argument("--temperature", type=float, default=1.0, help="Temperature for sampling.")

    parser.add_argument("--sys_msg_path", type=str, default="induce/prompt/system_message_memory.txt")
    parser.add_argument("--instruction_path", type=str, default="induce/prompt/instruction_memory.txt")
    parser.add_argument("--few_shot_path", type=str, default="induce/prompt/shopping_memory.md")
    parser.add_argument("--test_query_path", type=str, default="induce/prompt/test_query.txt")

    parser.add_argument("--template_id", type=str, default=None)
    parser.add_argument("--website", type=str, required=True,
                        choices=["shopping", "admin", "reddit", "gitlab", "map", 'all'])
    parser.add_argument("--config_dir", type=str, default="config_files")
    parser.add_argument("--results_dir", type=str, default="results")
    parser.add_argument("--result_id_list", type=str, nargs="+", default=None, help="E.g., '110_2_0 111_1'.")

    parser.add_argument("--write_tests_dir", type=str, default="debug_actions")
    parser.add_argument("--eval_with_gold", action="store_true")
    args = parser.parse_args()

    if args.model == "claude":
        args.model = "litellm/neulab/claude-3-5-sonnet-20241022"
    args.model = args.model.replace("litellm", "openai")

    # decide path for entire model output
    args = get_output_dir(args, key="workflow")
    if os.path.exists(args.output_dir):
        print(f"Output directory already exists: {args.output_dir}")
        names = sorted(os.listdir(args.output_dir), key=lambda x: int(x.split('.')[0]))
        paths = [os.path.join(args.output_dir, f) for f in names]
        responses = [open(p, 'r').read() for p in paths]
    else:  # induce new actions
        os.makedirs(args.output_dir, exist_ok=True)
        responses = induce_workflows()
    
    assert len(responses) == 1, "Only support one response for now."
    
    # write actions and run tests
    for i, resp in enumerate(responses):
        print(f"\n\n** Start Evaluating Response {i} **")
        write_workflows(resp)

        print(f"**Finish Evaluating Response {i} **\n\n")
