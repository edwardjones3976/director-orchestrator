#!/usr/bin/env python3
"""Append-only Git mailbox. Uses Git objects; never changes the checkout/index."""
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

BRANCH = 'codex/cloud-team-mailbox-20261004'
REMOTE = 'https://github.com/edwardjones3976/director-orchestrator.git'

def git(repo, *args, data=None, env=None, check=True):
    return subprocess.run(['git', '-C', str(repo), *args], input=data,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env=env, check=check)

def tip(args):
    result = git(args.repo, 'ls-remote', '--heads', args.remote, 'refs/heads/'+args.branch)
    if not result.stdout.strip():
        return None
    sha = result.stdout.decode().split()[0]
    git(args.repo, 'fetch', '--no-tags', '--no-write-fetch-head', args.remote, sha)
    return sha

def append(args, files, subject):
    # Every retry rebases only new immutable files onto the current mailbox tip.
    for attempt in range(4):
        parent = tip(args)
        with tempfile.TemporaryDirectory(prefix='director-mailbox-') as directory:
            env = dict(os.environ, GIT_INDEX_FILE=str(Path(directory)/'index'))
            git(args.repo, 'read-tree', parent or '--empty', env=env)
            for name, content in files.items():
                existing = git(args.repo, 'ls-files', '--error-unmatch', name, env=env, check=False)
                if existing.returncode == 0:
                    raise RuntimeError('Refusing to replace an existing mailbox file: '+name)
                blob = git(args.repo, 'hash-object', '-w', '--stdin', data=content).stdout.decode().strip()
                git(args.repo, 'update-index', '--add', '--cacheinfo', '100644,'+blob+','+name, env=env)
            tree = git(args.repo, 'write-tree', env=env).stdout.decode().strip()
            command = ['-c','user.name='+args.author,'-c','user.email='+args.email,'commit-tree',tree]
            if parent:
                command += ['-p',parent]
            commit = git(args.repo, *command, data=(subject+'\n').encode()).stdout.decode().strip()
        result = git(args.repo, 'push', args.remote, commit+':refs/heads/'+args.branch, check=False)
        if result.returncode == 0:
            confirmed = tip(args)
            # A concurrent append may have advanced the branch after our push.
            git(args.repo, 'merge-base', '--is-ancestor', commit, confirmed)
            return commit
        latest = tip(args)
        if latest == parent:
            raise RuntimeError('Git push failed without a concurrent update; check access (no secrets logged).')
    raise RuntimeError('Mailbox busy after four concurrent updates; retry later.')

def read(args):
    head = tip(args)
    if not head:
        return []
    names = git(args.repo,'ls-tree','-r','--name-only',head,'--','messages/').stdout.decode().splitlines()
    messages = []
    for name in names:
        item=json.loads(git(args.repo,'show',head+':'+name).stdout)
        if not args.to or item['to'] in (args.to,'all'):
            messages.append(item)
    return sorted(messages,key=lambda item:(item['created_at'],item['id']))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',default='.')
    parser.add_argument('--remote',default=REMOTE)
    parser.add_argument('--branch',default=BRANCH)
    parser.add_argument('--author',default='OpenAI Codex')
    parser.add_argument('--email',default='codex@openai.com')
    sub=parser.add_subparsers(dest='command',required=True)
    init=sub.add_parser('init'); init.add_argument('--readme',required=True)
    send=sub.add_parser('send'); send.add_argument('--from',dest='sender',required=True)
    send.add_argument('--to',required=True); send.add_argument('--body-file',required=True)
    send.add_argument('--reply-to',default=None)
    receive=sub.add_parser('read'); receive.add_argument('--to')
    args=parser.parse_args()
    if args.command=='read':
        print(json.dumps(read(args),indent=2)); return
    if args.command=='init':
        if tip(args):
            raise RuntimeError('Mailbox branch already exists; refusing reinitialization.')
        files={'README.html':Path(args.readme).read_bytes(),'mailbox.py':Path(__file__).read_bytes()}
        print(append(args,files,'Initialize isolated cloud team mailbox')); return
    body=Path(args.body_file).read_text(encoding='utf-8-sig')
    if not body.strip() or len(body.encode())>65536:
        raise ValueError('Message must be nonempty and no more than 64 KiB.')
    item=dict(id=uuid.uuid4().hex,created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
              sender=args.sender,to=args.to,reply_to=args.reply_to,body=body)
    filename='messages/'+item['id']+'.json'
    commit=append(args,{filename:(json.dumps(item,indent=2)+'\n').encode()},'Mailbox message '+item['id'])
    print(json.dumps({'message_id':item['id'],'commit':commit,'status':'published; recipient acknowledgement pending'}))

if __name__=='__main__':
    main()
