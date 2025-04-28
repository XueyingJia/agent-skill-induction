import os
import json
import argparse
import subprocess
from subprocess import Popen
from collections import defaultdict


def parse_task_ids(task_id_str: str) -> list[str]:
    chunks = [c.strip() for c in task_id_str.split(",")]
    task_id_list = []
    for c in chunks:
        s, e = [int(n.strip()) for n in c.split("-")]
        task_id_list.extend([str(i) for i in range(s, e+1)])
    return task_id_list

def load_tasks_by_website(config_dir='config_files', website=None, max_tasks=30):
    """
    Load task IDs from JSON files in config_files directory, filtered by website.
    
    Args:
        config_dir (str): Directory containing task configuration JSON files
        website (str): Filter tasks by website category
        max_tasks (int): Maximum number of tasks to load per website category
    
    Returns:
        list: List of task IDs that match the website filter
    """
    task_ids = []
    count = 0
    
    # Ensure the directory exists
    if not os.path.exists(config_dir):
        print(f"Error: Directory '{config_dir}' not found")
        return []
    
    # Get all JSON files and sort them numerically
    try:
        config_files = [f for f in os.listdir(config_dir) if f.endswith('.json')]
        config_files.sort(key=lambda x: int(os.path.splitext(x)[0]) if os.path.splitext(x)[0].isdigit() else float('inf'))
    except Exception as e:
        print(f"Error listing files in '{config_dir}': {e}")
        return []
    
    # Process each file
    for file_name in config_files:
        if count >= max_tasks:
            break
            
        file_path = os.path.join(config_dir, file_name)
        try:
            with open(file_path, 'r') as f:
                config = json.load(f)
            
            # Check if the config has the 'sites' field
            if 'sites' not in config:
                continue
                
            # Get the site category
            sites = config.get('sites', [])
            
            # Check if task matches the website filter
            if len(sites) == 1 and sites[0] == website:
                task_id = os.path.splitext(file_name)[0]
                task_ids.append(task_id)
                count += 1
        except Exception as e:
            print(f"Error loading {file_path}: {e}")

    print(f"Loaded {len(task_ids)} task IDs for website '{website}'")
    return task_ids

# %% Baseline

def run_vanilla():
    task_id_list = parse_task_ids(args.task_ids)

    for tid in task_id_list:
        # step 1: task solving
        process = Popen([
            "python", "run_demo.py",
            "--task_name", f"webarena.{tid}",
            "--headless",
        ])
        try:
            stdout, stderr = process.communicate(timeout=200)
            print(stdout)
        except subprocess.TimeoutExpired as e:
            process.kill()
            stdout, stderr = process.communicate() # Clean up resources
            print(f"Process timed out after {e.timeout} seconds.")
            print(stderr)

# %% AWM

def run_awm():

    debug_mode = True
    task_id_list = load_tasks_by_website(config_dir='config_files', website=args.website, max_tasks=30)

    for tid in task_id_list:

        # step 0: retrieve workflows
        with open(f"config_files/{tid}.json", 'r') as f:
            config_data = json.load(f)
        natural_language_description = config_data["intent"]
        process = Popen([
            "python", "chroma_retriever.py",
            "--task_id", tid,
            "--task_type", "all",
            "--natural_language_description", natural_language_description,
            "--use_distance",
            "--distance_threshold", "2.0"

            ])
        process.wait()
        if debug_mode:
            input("[0] Completed workflow retrieval")

        # step 1: task solving
        process = Popen([
            "python", "run_demo.py",
            "--task_name", f"webarena.{tid}",
            "--memory_path", f"retrieval_outputs/retrieval_result_{tid}.json",
            "--headless",
        ])
        process.wait()
        if debug_mode:
            input("[1] Completed task solving")

        # step 2: eval traj
        process = Popen([
            "python", "-m", "autoeval.evaluate_trajectory",
            "--result_dir", f"results/webarena.{tid}"
        ])
        process.wait()
        path = f"results/webarena.{tid}/gpt-4o-2024-05-13_autoeval.json"
        is_correct = json.load(open(path))[0]["rm"]  # bool
        if not is_correct: continue
        if debug_mode:
            input("[2] Completed evaluated trajectory (true)")

        # step 3: induce workflows
        process = Popen([
            "python", "-m", "results.calc_valid_steps",
            "--clean_and_store", "--result_dir", f"results/webarena.{tid}",
        ])
        process.wait()  # output 'clean_steps.json'
        if debug_mode:
            input("[3.1] Completed clean trajectory")
        

        process = Popen([
            "python", "-m", "induce.induce_memory",
            "--website", 'all',
            "--result_id_list", f'results/webarena.{tid}'
        ])
        process.wait()  # write to 'workflows/{args.website}.txt'
        if debug_mode:
            input("[3.2] Completed induced workflow")

# %% ASI
def run_asi():
    task_id_list = parse_task_ids(args.task_ids)
    for tid in task_id_list:
        # step 1: task solving
        process = Popen([
            "python", "run_demo.py",
            "--task_name", f"webarena.{tid}",
            "--websites", args.website,
            "--headless"
        ])
        try:
            stdout, stderr = process.communicate(timeout=300)
            print("Process completed successfully:")
            print(stdout)
        except subprocess.TimeoutExpired as e:
            process.kill()
            stdout, stderr = process.communicate() # Clean up resources
            print(f"Process timed out after {e.timeout} seconds.")
            print(stderr)
            continue
        # input("[1] Completed task solving")
        path = f"results/webarena.{tid}/summary_info.json"
        if json.load(open(path, 'r'))["n_steps"] < 3: continue

        # step 2: eval traj
        process = Popen([
            "python", "-m", "autoeval.evaluate_trajectory",
            "--result_dir", f"results/webarena.{tid}",
        ])
        process.wait()
        path = f"results/webarena.{tid}/gpt-4o-2024-05-13_autoeval.json"
        is_correct = json.load(open(path))[0]["rm"]  # bool
        if not is_correct: continue
        # input("[2.1] Completed evaluated trajectory (true)")
        process = Popen([
            "python", "-m", "results.calc_valid_steps",
            "--clean_and_store", "--result_dir", f"results/webarena.{tid}",
        ])
        process.wait()  # output 'clean_steps.json'
        # input("[2.2] Completed clean trajectory")

        # step 3: induce actions
        process = Popen([
            "python", "-m", "induce.induce_actions_full",
            "--website", args.website,
            "--result_id_list", tid,
        ])
        try:
            stdout, stderr = process.communicate(timeout=200)
            print(stdout)
        except subprocess.TimeoutExpired as e:
            process.kill()
            stdout, stderr = process.communicate() # Clean up resources
            print(f"Process timed out after {e.timeout} seconds.")
            print(stderr)
        # input("[3] Completed induced workflow")

        # intermediate supervision
        # cont = input("Continue? (y/n)")


# %% Verified, Program
def run_veri_program():
    task_id_list = parse_task_ids(args.task_ids)
    for tid in task_id_list:
        # step 1: task solving with memory input
        process = Popen([
            "python", "run_demo.py", "--headless",
            "--task_name", f"webarena.{tid}",
            "--memory_path", f"workflows/{args.website}.txt",
        ])
        try:
            stdout, stderr = process.communicate(timeout=300)
            print("Process completed successfully:")
            print(stdout)
        except subprocess.TimeoutExpired as e:
            process.kill()
            stdout, stderr = process.communicate() # Clean up resources
            print(f"Process timed out after {e.timeout} seconds.")
            print(stderr)
            continue
        # input("[1] Completed task solving")
        path = f"results/webarena.{tid}/summary_info.json"
        if json.load(open(path, 'r'))["n_steps"] < 3: continue

        # step 2: eval traj
        process = Popen([
            "python", "-m", "autoeval.evaluate_trajectory",
            "--result_dir", f"results/webarena.{tid}",
        ])
        process.wait()
        path = f"results/webarena.{tid}/gpt-4o-2024-05-13_autoeval.json"
        is_correct = json.load(open(path))[0]["rm"]  # bool
        if not is_correct: continue
        # input("[2.1] Completed evaluated trajectory (true)")
        process = Popen([
            "python", "-m", "results.calc_valid_steps",
            "--clean_and_store", "--result_dir", f"results/webarena.{tid}",
        ])
        process.wait()  # output 'clean_steps.json'
        # input("[2.2] Completed clean trajectory")

        # step 3: induce actions
        orignal_content = open(f"actions/{args.website}.py", 'r').read()
        process = Popen([
            "python", "-m", "induce.induce_actions_full",
            "--website", args.website,
            "--result_id_list", tid,
        ])
        try:
            stdout, stderr = process.communicate(timeout=200)
            print(stdout)
        except subprocess.TimeoutExpired as e:
            process.kill()
            stdout, stderr = process.communicate() # Clean up resources
            print(f"Process timed out after {e.timeout} seconds.")
            print(stderr)
            continue
        updated_content = open(f"actions/{args.website}.py", 'r').read()
        if orignal_content != updated_content:
            new_content = updated_content[len(orignal_content):].strip()
            with open(f"workflows/{args.website}.txt", 'a') as f:
                f.write(new_content + "\n\n")
        # input("[3] Completed induced workflow")

        # intermediate supervision
        # cont = input("Continue? (y/n)")

# %% Memory-Augmented ASI

def run_mem_asi():
    task_id_list = parse_task_ids(args.task_ids)
    for tid in task_id_list:
        # step 1: task solving
        process = Popen([
            "python", "run_demo.py",
            "--task_name", f"webarena.{tid}",
            "--websites", args.website,
            "--headless"
        ])
        try:
            stdout, stderr = process.communicate(timeout=300)
            print("Process completed successfully:")
            print(stdout)
        except subprocess.TimeoutExpired as e:
            process.kill()
            stdout, stderr = process.communicate() # Clean up resources
            print(f"Process timed out after {e.timeout} seconds.")
            print(stderr)
            continue
        # input("[1] Completed task solving")
        path = f"results/webarena.{tid}/summary_info.json"
        if json.load(open(path, 'r'))["n_steps"] < 3: continue

        # step 2: eval traj
        process = Popen([
            "python", "-m", "autoeval.evaluate_trajectory",
            "--result_dir", f"results/webarena.{tid}",
        ])
        process.wait()
        path = f"results/webarena.{tid}/gpt-4o-2024-05-13_autoeval.json"
        is_correct = json.load(open(path))[0]["rm"]  # bool
        if not is_correct: continue
        # input("[2.1] Completed evaluated trajectory (true)")
        process = Popen([
            "python", "-m", "results.calc_valid_steps",
            "--clean_and_store", "--result_dir", f"results/webarena.{tid}",
        ])
        process.wait()  # output 'clean_steps.json'
        # input("[2.2] Completed clean trajectory")

        # step 3: induce actions
        nlines = len(open(f"actions/{args.website}.py", 'r').readlines())
        process = Popen([
            "python", "-m", "induce.induce_actions_full",
            "--website", args.website,
            "--result_id_list", tid,
        ])
        try:
            stdout, stderr = process.communicate(timeout=300)
            print("Process completed successfully:")
            print(stdout)
        except subprocess.TimeoutExpired as e:
            process.kill()
            stdout, stderr = process.communicate() # Clean up resources
            print(f"Process timed out after {e.timeout} seconds.")
            print(stderr)
            continue
        input("[3] Completed induced actions")

        # step 4: induce memory
        new_nlines = len(open(f"actions/{args.website}.py", 'r').readlines())
        if new_nlines == nlines: continue
        if new_nlines == nlines: task_abbr = tid
        else: task_abbr = f"{tid}_test"

        output_path = f"results/webarena.{task_abbr}/cleaned_steps.json"
        if not os.path.exists(output_path):
            process = Popen([
                "python", "-m", "results.calc_valid_steps",
                "--clean_and_store",
                "--result_dir", f"results/webarena.{task_abbr}",
            ])
            process.wait()  # output 'clean_steps.json'
            # input("[4.1] Completed clean trajectory")
        process = Popen([
            "python", "-m", "induce.induce_memory",
            "--website", args.website,
            "--result_id_list", task_abbr,
        ])
        process.wait()
        input("[4.2] Completed induce memory")

        # intermediate supervision
        cont = input("Continue? (y/n)")


# %% Main Pipeline

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", type=str, required=True,
                        choices=["vanilla", "awm", "asi", "mem_asi", "veri_program", "veri_text"])
    parser.add_argument("--website", type=str, required=True, default='all',
                        choices=["shopping", "admin", "reddit", "gitlab", "map", 'all'])
    parser.add_argument("--task_ids", type=str, required=True,
                        help="xxx-xxx,xxx-xxx")

    args = parser.parse_args()

    if args.experiment == "vanilla":
        run_vanilla()
    elif args.experiment == "awm":
        run_awm()
    elif args.experiment == "asi":
        run_asi()
    elif args.experiment == "mem_asi":
        run_mem_asi()
