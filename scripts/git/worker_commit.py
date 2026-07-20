import argparse
import subprocess
import os
import shutil

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--message-file', required=True)
    parser.add_argument('--scope', nargs='+')
    parser.add_argument('--index-file')
    args = parser.parse_args()

    env = os.environ.copy()
    if args.index_file:
        # Copy the current index to the new index file to preserve tracked files
        git_dir = subprocess.check_output(['git', 'rev-parse', '--git-dir']).decode().strip()
        default_index = os.path.join(git_dir, 'index')
        if os.path.exists(default_index):
            shutil.copy2(default_index, args.index_file)
        env['GIT_INDEX_FILE'] = args.index_file

    # Stage the files
    for f in args.scope:
        subprocess.run(['git', 'add', f], env=env, check=True)

    # Commit using the message-file
    subprocess.run(['git', 'commit', '-F', args.message_file], env=env, check=True)

if __name__ == '__main__':
    main()
