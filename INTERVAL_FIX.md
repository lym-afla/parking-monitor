# Interval Persistence Fix

## Problem
When changing the monitoring interval via the Telegram bot, the interval would reset back to the original value (120 seconds) after a short time.

## Root Cause
In [monitor.py:73](monitor.py:73), the state was loaded **once** at service startup:
```python
state = load_state()  # Loaded once, never reloaded
```

### What Was Happening:
1. Monitor service starts with `interval=120`
2. User changes interval to 30 via Telegram bot → `state.json` updated
3. Monitor continues using its **in-memory** state with `interval=120`
4. Monitor saves state → **overwrites** the Telegram bot's change back to 120!

The monitor service and Telegram bot were both reading and writing to the same `state.json` file, but the monitor never reloaded it to see the bot's changes.

## Solution
Modified [monitor.py](monitor.py:79) to reload state at the beginning of each loop iteration:

```python
def main():
    # ... initialization code ...

    while True:
        try:
            # Reload state to pick up interval changes from Telegram bot
            state = load_state()  # ← NEW: Reload every iteration

            # ... check parking, update counters ...
            save_state(state)

            interval = state.get("interval", CHECK_INTERVAL_SECONDS)
            # ... logging ...

        except Exception as e:
            # Also reload in error handler
            state = load_state()  # ← NEW: Also reload on errors
            state["error"] = str(e)
            save_state(state)
            interval = state.get("interval", CHECK_INTERVAL_SECONDS)

        time.sleep(interval)  # Use the current interval
```

### Key Changes:
1. **Line 79**: Reload state at the start of each loop iteration
2. **Line 102**: Also reload state in error handler to avoid overwriting interval changes
3. **Line 107**: Use the interval variable (now correctly defined in both paths)

## Testing
Verified the fix works correctly:

1. Service started with `interval=120`
2. Changed interval to 30 seconds in `state.json`
3. Monitor picked up the change on next iteration:
   ```
   [21:56:40] Check #38 complete. Next check in 30 seconds.
   [21:57:11] Starting website check...
   ```
4. Time difference: 31 seconds ✅ (30s interval + 1s for check execution)

## Impact
✅ Interval changes via Telegram bot now persist correctly
✅ No restart required - changes take effect on next check cycle
✅ All statistics (checks, hits) continue to work correctly
✅ Services remain stable and running

## Deployment
- **Local repository**: Committed in `e2a06cf`
- **Remote server**: Applied and tested
- **Service status**: ✅ Running correctly with fix applied

## Git Permissions Fix
Also fixed git permissions issue where `user1` couldn't perform git operations:

```bash
sudo chown -R parking_user:parking_user /opt/parking_monitor
sudo chmod -R g+w /opt/parking_monitor
sudo find /opt/parking_monitor -type d -exec chmod g+s {} \;
git config core.sharedRepository group
```

Now both `user1` and `parking_user` can work with git in the shared directory.

## Date Fixed
2025-12-17

## Status
✅ RESOLVED and TESTED - Interval changes persist correctly
