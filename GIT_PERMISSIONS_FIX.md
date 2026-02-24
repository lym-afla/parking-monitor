# Git Permission Fix for Remote Server

## Problem
When trying to run `git pull` on the remote server, you encountered:
```
error: cannot open .git/FETCH_HEAD: Permission denied
```

## Root Cause
The `/opt/parking_monitor` directory and `.git` folder were owned by `parking_user:parking_user`, but you were running git commands as `user1`. Even though both users are in each other's groups, the git repository was not configured for shared access.

## Solution Applied

### 1. Made Git Repository Group-Writable
```bash
cd /opt/parking_monitor
sudo chmod -R g+w .git
sudo find .git -type d -exec chmod g+s {} \;
git config core.sharedRepository group
```

### 2. Made Entire Directory Shared
```bash
sudo chmod -R g+w .
sudo find . -type d -exec chmod g+s {} \;
```

The `g+s` (setgid) bit ensures that new files inherit the group ownership.

### 3. Synchronized Branches
After fixing permissions, we had divergent branches (duplicate commits). Resolved by:
```bash
git reset --hard origin/main
```

## Verification
```bash
$ git pull --tags origin main
Already up to date.
From https://github.com/lym-afla/parking-monitor
 * branch            main       -> FETCH_HEAD
```

## Current Setup
- Both `user1` and `parking_user` can now perform git operations
- Services continue running correctly as `parking_user`
- Directory structure:
  - Owner: `parking_user:parking_user`
  - Permissions: Group-writable with setgid
  - Git config: `core.sharedRepository = group`

## Usage
Now you can use git normally on the remote server:
```bash
ssh cloudru-server
cd /opt/parking_monitor
git pull --tags origin main
git status
git commit -am "message"
# Note: Push requires GitHub credentials or SSH keys
```

## Services Status
Both services continue to run without interruption:
- ✅ parking-service-monitor.service: Active (running)
- ✅ parking-service-bot.service: Active (running)
