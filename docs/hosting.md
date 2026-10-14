# Hosting the game for friends

How to put the 6-seat game on a small server so a group of friends can play from
their own computers. Written for a desktop browser; phones are not supported.

What the players get: an address like `http://203.0.113.20/` and one shared
passcode. They type the passcode once, pick a name, and join the table.

## What "hosted" changes

Setting `REDTEN_HOSTED=1` (see `src/hosting.py`) turns all of this on. Without it
the server behaves as it always has on a laptop.

- **Only the game is served.** The lobby is at `/`. Training, replay, AI battles
  and spectating answer "not found".
- **A passcode is required** for every page, API call and game connection. The
  server refuses to start without `REDTEN_PASSCODE`.
- **Empty seats are played by the Rule or Search AI.** The ML, multi-agent and
  LLM players are not offered, so the server needs no torch and no model files.
- **At most 3 tables** are open at once, and a table nobody has been connected
  to for 10 minutes is closed.

In every mode, a seat now belongs to the player who took it: a page reload or a
dropped connection gets the same seat back. The AI plays for a player who has
dropped, and after 60 seconds the seat is free for someone else. One person can
open several tabs or windows and hold a seat in each.

## What it costs

One Amazon Lightsail server on the $5/month plan: 0.5 GB of memory, 20 GB of
disk and the public address are included. Confirm the price in the Lightsail
console before you apply; AWS bills every public IPv4 address, so there is no
cheaper way to have a fixed address.

Measured on a laptop: the server uses about 60–70 MB of memory with a game
running. The Rule AI costs almost nothing. The Search AI used about 16 seconds
of CPU per hand with six AI seats, and its slowest single decision took 2
seconds; expect a few times that on the small server. If the table feels
sluggish, create tables with the Rule AI.

## Create the server

You need an AWS account, Terraform, and an SSH key. The branch named in
`repo_branch` must be pushed to GitHub first: the server downloads the game
from there.

```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars: passcode, your address for SSH, your public key
terraform init
terraform plan      # read what it will create
terraform apply     # type yes
```

`terraform apply` prints the address. The first start takes two or three
minutes while the server installs itself. Then open the address in a browser.

It creates four things in Lightsail, all named `redten`: the server, a fixed
address attached to it, a firewall (port 80 for everyone, SSH for your address
only) and your SSH key.

The bundle and image names in `variables.tf` (`nano_3_0`, `ubuntu_24_04`) were
taken from AWS's documentation, not checked against a live account. If
`terraform plan` rejects one, list the current names in the Lightsail console
under "Create instance" and set `bundle_id` or `blueprint_id`.

`terraform.tfvars` and the Terraform state file contain the passcode. Both are
ignored by git; keep them on your machine.

## Looking after it

Log in with the `ssh` line that `terraform apply` printed.

| To do this | Run this on the server |
|---|---|
| See whether it is running | `systemctl status redten` |
| Watch the server log | `journalctl -u redten -f` |
| Restart it | `sudo systemctl restart redten` |
| Change the passcode | `sudo nano /etc/redten.env`, then restart |
| Install the newest version | `sudo bash /opt/redten/app/deploy/install.sh` |
| See why first boot failed | `sudo tail -50 /var/log/cloud-init-output.log` |

A restart or an update ends any game in progress, so do it between sessions.
Changing the passcode logs everyone out; they type the new one once.

Game logs for the 🚩 Flag button are in `/opt/redten/app/debug_logs/` and are
deleted after 30 days. To inspect a flag, copy that room's `.jsonl` file into
`debug_logs/` on your own machine and run `python3 src/debug_inspect.py`.

## Taking it down

```bash
cd deploy/terraform
terraform destroy
```

This deletes the server and releases the address, and the bill stops. A new
server later gets a new address.

## Any other Ubuntu server

`deploy/install.sh` does the whole installation and works without Terraform or
AWS. On a fresh Ubuntu 24.04 machine with a fixed public address and port 80
open:

```bash
curl -fsSLO https://raw.githubusercontent.com/yszhangit/red-ten/hosted-play/deploy/install.sh
sudo REDTEN_PASSCODE=your-passcode bash install.sh
```

It creates a `redten` user, downloads only the server and web client (under
1 MB, not the models or training data), installs four Python packages into
`/opt/redten/venv`, writes `/etc/redten.env`, and starts a systemd service on
port 80 that restarts by itself.

## Plain HTTP: what you are accepting

The game is served over `http://` on a bare address, with no certificate.

- The passcode and the cards travel unencrypted. Anyone on the same Wi-Fi or
  network path could read them. For a card game among friends that is a small
  risk; do not reuse a passcode that matters elsewhere.
- Browsers show "Not secure" next to the address.
- The 🚩 Flag button cannot copy to the clipboard without HTTPS. It shows the
  reference on screen instead; copy it by hand.

To add HTTPS later you need a hostname pointing at the server. Then set
`REDTEN_HOST=127.0.0.1` and `REDTEN_PORT=5173` in `/etc/redten.env`, put Caddy
in front (`reverse_proxy 127.0.0.1:5173`), and open port 443. The login cookie
is marked secure automatically once requests arrive over HTTPS.

## Not there yet

- No timer for a player who is connected but not playing: the table waits.
- No phone or tablet layout.
- One server process holds every game in memory. A restart ends them.
