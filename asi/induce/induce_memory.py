import os
import json
import litellm
import argparse
from induce.utils import get_output_dir, get_task_id
from datasets import load_dataset  # Import load_dataset from the datasets library

# %% Induce Memory
def get_test_query(hf_path, n=1):
    # directly load from huggingface dataset
    dataset = load_dataset(hf_path, trust_remote_code=True, token=os.getenv("HF_READ_TOKEN"))
    dataset = dataset['train']

    # collect n objectives and corresponding queries
    task_query = {}
    i = 0
    while i < len(dataset):
        if len(task_query.keys()) >= n:
            break
        task_name = dataset[i]['task_name']
        if task_name not in task_query:
            objective = dataset[i]['objective']
            task_query[task_name] = f"## Task: {objective}\n"
        while dataset[i]['task_name'] == task_name:
            example = dataset[i]
            task_query[task_name] += f"<think>{example['thought']}</think>\n<action>{example['action']}</action>\n"
            i += 1
    return '\n'.join(task_query.values())

def get_test_query_via_complete_trajectory(hf_path, n=1):
    # directly load from huggingface dataset
    dataset = load_dataset(hf_path, trust_remote_code=True, token=os.getenv("HF_READ_TOKEN"))
    dataset = dataset['train']

    # collect n objectives and corresponding queries
    goal_trajectory = {}
    i = 0
    while i < len(dataset):
        if len(goal_trajectory.keys()) >= n:
            break
        goal_id = dataset[i]['goal_id']
        if goal_id not in goal_trajectory:
            goal = dataset[i]['goal']
            goal_trajectory[goal_id] = f"## Task: {goal}\n"
        j = 1
        while dataset[i]['goal_id'] == goal_id:
            goal_trajectory[goal_id] += f"## Example {j}:\n"
            goal_trajectory[goal_id] += dataset[i]['trajectory'] + '\n\n'
            i += 1
            j += 1
    return '\n'.join(goal_trajectory.values())
    
def induce_workflows(hf_path) -> list[str]:
    if hf_path == "XueyingJia/deduplicated_Auprva_exploration_trajectory":
        # get test query from huggingface dataset
        test_query = get_test_query_via_complete_trajectory(hf_path)
    else:
        # get test query from huggingface dataset
        test_query = get_test_query(hf_path)
    if test_query=='': return []

    messages = [{"role": "system", "content": open(args.sys_msg_path).read()}]
    messages += [{"role": "user", "content": open(args.instruction_path).read()}]
    messages += [{"role": "user", "content": open(args.few_shot_path).read()}]
    messages += [{"role": "user", "content": "## Existing Workflows\n" + open(args.write_workflow_path).read()}]
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
    return name


def update_workflows(workflow: str, existing_workflows: list[str]) -> tuple[bool, list[str]]:
    """Update the existing workflows given the potentially topically similar new item.
    - If the new workflow does not overlap with any existing workflows, add it => True, []
    - If the new workflow topically overlap:
      - If the new workflow is better, replace the existing workflow => True, [existing_workflow_name]
        - If the existing workflow is better, keep the existing workflow => False, []
    """
    name = get_workflow_name(workflow)
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
    response = response.choices[0].message.content
    if "workflow 1" in response.lower(): return workflow1
    elif "workflow 2" in response.lower(): return workflow2
    else: return None

def write_workflows(response: str) -> None:
    # get newly induced workflows
    workflows = extract_code_pieces(response, start='"""', end='"""', do_split=False)
    workflows = [w for w in workflows if ("Task" in w) and ("Action Trajectory" in w)]

    # load existing workflows
    existing_workflows = open(args.write_workflow_path, 'r').read().split("Task:")
    existing_workflows = ["Task:"+w for w in existing_workflows if len(w) > 0]
    existing_workflows = [w.strip() for w in existing_workflows]
    existing_workflows = [w for w in existing_workflows if len(w) > 0]
    existing_workflow_names = [get_workflow_name(w) for w in existing_workflows]

    # update workflows
    new_workflows = []
    for w in workflows:
        add_new, names_to_remove = update_workflows(w, existing_workflows)
        existing_workflows = [
            ew for n,ew in zip(existing_workflow_names, existing_workflows)
            if n not in names_to_remove
        ]
        if add_new: new_workflows.append(w)

    # rewrite the entire workflow memory
    with open(args.write_workflow_path, 'w') as fw:
        fw.write('\n\n'.join(existing_workflows + new_workflows))


# %% Overall pipeline

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, default="claude", choices=["gpt-4o", "claude"])
    parser.add_argument("--num_responses", type=int, default=1, help="Number of responses to generate.")
    parser.add_argument("--temperature", type=float, default=1.0, help="Temperature for sampling.")

    parser.add_argument("--sys_msg_path", type=str, default="induce/prompt/system_message_memory.txt")
    #TODO: deecide you want to have how many steps in the induced workflows, fine with 2-5 fow now
    parser.add_argument("--instruction_path", type=str, default="induce/prompt/instruction_memory.txt")
    parser.add_argument("--few_shot_path", type=str, default="induce/prompt/shopping_memory.md")
    parser.add_argument("--test_query_path", type=str, default="XueyingJia/nnetnav-wa-trajectory")

    parser.add_argument("--website", type=str, required=True,
                        choices=["shopping", "admin", "reddit", "gitlab", "map", "mixed"])

    # TODO: write to huggingface instead of local files, remove eval
    parser.add_argument("--write_workflow_path", type=str, default=None)
    parser.add_argument("--output_dir", type=str, default="workflows")
    args = parser.parse_args()

    if args.model == "claude":
        args.model = "litellm/neulab/claude-3-5-sonnet-20241022"
    args.model = args.model.replace("litellm", "openai")

    if args.write_workflow_path is None:
        args.write_workflow_path = os.path.join("workflows", f"{args.website}.txt")

    # # decide path for entire model output
    # args = get_output_dir(args, key="workflow")
    # if os.path.exists(args.output_dir):
    #     print(f"Output directory already exists: {args.output_dir}")
    #     names = sorted(os.listdir(args.output_dir), key=lambda x: int(x.split('.')[0]))
    #     paths = [os.path.join(args.output_dir, f) for f in names]
    #     responses = [open(p, 'r').read() for p in paths]
    # else:  # induce new actions
    
    os.makedirs(args.output_dir, exist_ok=True)
    responses = induce_workflows(args.test_query_path)
    
    assert len(responses) == 1, "Only support one response for now."
    print('induced workflow:')
    print(responses[0])
    
    # write actions and run tests
    # for i, resp in enumerate(responses):
    #     print(f"\n\n** Start Evaluating Response {i} **")
    #     write_workflows(resp)

    #     print(f"**Finish Evaluating Response {i} **\n\n")
    #     cont = input("Continue? [y/n]")
