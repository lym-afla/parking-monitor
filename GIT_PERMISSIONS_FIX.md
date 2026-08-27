# Archived Git-permissions note

This historical note is superseded. The checkout, `.git` directory, venv, and
root-invoked scripts must remain root-owned and read-only to all four service
users. Granting a network-facing service write access to Git-controlled or
root-invoked code crosses the production trust boundary.

Use the verified `sudo parking-monitor update` transaction described in
[DEPLOYMENT.md](DEPLOYMENT.md). It stages and tests the target revision before a
fast-forward cutover and restores the recorded revision and runtime snapshot if
cutover fails.
