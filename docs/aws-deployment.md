# Deploying to AWS, step by step

This puts the hosted game (see `docs/hosting.md`) on one small Amazon Lightsail server
using the Terraform config in `deploy/terraform/`. At the end you have an address like
`http://203.0.113.20/` to give the players, together with the passcode.

**Status of this procedure:** `terraform apply` has been run with this config and
created the server (2026-10-14). On that run the first-boot install did not start,
because Lightsail runs the launch script with plain `sh`; that is fixed, and the fixed
script was checked locally under `sh` but not yet on a newly created server. If the game
is not up a few minutes after `apply`, step 8 shows how to start the install by hand.

## What it creates and what it costs

| Resource (all named `redten…`) | What it is |
|---|---|
| Lightsail instance | Ubuntu 24.04, the $5/month plan: 0.5 GB memory, 20 GB disk, public IPv4 included |
| Static IP | The fixed address players use. No charge while attached to the instance |
| Firewall rules | Port 80 open to everyone; port 22 (SSH) open to your address only |
| Key pair | Your SSH public key, so you can log in |

Expect about **$5 a month** for as long as it exists. Lightsail sometimes shows a free
trial on this plan; do not count on it. Check the price on the plan picker in the
Lightsail console before applying. `terraform destroy` (step 10) stops the bill.

## 1. Check the tools

```bash
terraform version        # 1.5 or newer
ls ~/.ssh/*.pub          # a public key to install on the server
ls ~/.aws/credentials    # AWS access keys
```

If `~/.aws/credentials` has more than one profile, choose the account that should pay
for this and select it for the rest of the session:

```bash
export AWS_PROFILE=default     # or the name of the profile to use
```

The access keys need permission to manage Lightsail (an administrator user has it; a
restricted user needs `lightsail:*`).

## 2. Put a spending alarm on the account (once)

In the AWS console: **Billing and Cost Management → Budgets → Create budget → Zero spend
budget** or a monthly cost budget of a few dollars, with your email. This is the safety
net if something is left running.

## 3. Push the branch the server installs from

The server downloads the game from GitHub when it first boots, so the branch must be
there, and the repository must be readable without a login (it is public today).

```bash
git push origin hosted-play
```

If you deploy a different branch or fork, set `repo_branch` / `repo_url` in step 4.

## 4. Fill in the settings

```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars
curl https://checkip.amazonaws.com      # your public address, for SSH
```

Edit `terraform.tfvars`:

```hcl
passcode            = "pick-a-passcode"       # 6-64 letters, digits, - _ .
ssh_allowed_cidr    = "198.51.100.23/32"      # the address printed above, plus /32
ssh_public_key_path = "~/.ssh/id_rsa.pub"     # one of your public keys

# region = "us-east-1"                        # nearest the players
```

`terraform.tfvars` holds the passcode and is ignored by git. Do not commit it.

## 5. Preview

```bash
terraform init
terraform plan
```

`init` downloads the AWS provider (a few hundred MB, once). `plan` contacts AWS but
changes nothing. It should end with:

```
Plan: 5 to add, 0 to change, 0 to destroy.
```

The five are the instance, the key pair, the static IP, its attachment, and the
firewall rules. If `plan` reports an error instead, see step 8.

## 6. Create it

```bash
terraform apply        # review, then type: yes
```

It takes about a minute and prints:

```
address = "http://203.0.113.20/"
ssh     = "ssh ubuntu@203.0.113.20"
```

Billing starts now.

## 7. Wait for the first boot, then check

The server installs the game by itself on first boot. Allow two to three minutes, then:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://203.0.113.20/login     # expect 200
```

Open the address in a browser, enter the passcode, create a table, and open the same
address in a second browser to join it. That is the whole check.

Then give the players two things: the address and the passcode.

## 8. If something goes wrong

| What you see | Likely cause | What to do |
|---|---|---|
| `plan` or `apply`: invalid `bundle_id` or `blueprint_id` | AWS renamed the plan or image | In the Lightsail console, start "Create instance" to see current plans and images, then set `bundle_id` / `blueprint_id` in `terraform.tfvars` |
| `apply`: invalid availability zone | The region's first zone is not `…a` | Set `availability_zone`, for example `"us-west-2b"` |
| `apply`: key pair error | Key type or file not accepted | Point `ssh_public_key_path` at an RSA or ed25519 `.pub` file |
| `apply`: not authorized | Wrong profile, or no Lightsail permission | Check `AWS_PROFILE`; add `lightsail:*` |
| `curl` gives no answer after 5 minutes | First-boot install failed | Log in (below), read the install log, and run the install by hand |
| Browser cannot connect, `curl` on the server works | Firewall | Check the instance's Networking tab shows port 80 open |

To look inside the server:

```bash
ssh ubuntu@203.0.113.20
sudo tail -50 /var/log/cloud-init-output.log    # what the first-boot install printed
systemctl status redten                         # is the game running
journalctl -u redten -n 50                      # its recent log
```

The most common first-boot failure is step 3 skipped: the log shows git failing to find
the branch. Push it, then run the install again on the server:

```bash
curl -fsSLO https://raw.githubusercontent.com/yszhangit/red-ten/hosted-play/deploy/install.sh
sudo REDTEN_PASSCODE=pick-a-passcode bash install.sh
```

If SSH itself is refused, your address has changed since step 4. Update
`ssh_allowed_cidr` and run `terraform apply` again; only the firewall rule changes.

## 9. Afterwards

| To do this | How |
|---|---|
| Install a newer version | Push it, then on the server: `sudo bash /opt/redten/app/deploy/install.sh` |
| Change the passcode | On the server: `sudo nano /etc/redten.env`, then `sudo systemctl restart redten` |
| Restart | `sudo systemctl restart redten` |
| Watch the log | `journalctl -u redten -f` |
| Rebuild the server from scratch, same address | On your machine: `terraform apply -replace=aws_lightsail_instance.redten` |

A rebuild deletes the server and creates a new one that installs itself again; the
address stays, because the static IP is a separate resource. SSH will warn that the
host key changed: run `ssh-keygen -R <address>` once.

`ssh_allowed_cidr` limits SSH only; the game on port 80 is open to everyone and guarded
by the passcode. Use your own address with `/32`. If your address changes and SSH is
refused, update the value and run `terraform apply`; only the firewall rule changes.

An update or restart ends any game in progress; do it between sessions. The passcode in
`terraform.tfvars` is used only when the server is first created: change it on the server
as above, and editing the file later has no effect on a running server.

## 10. Take it down

```bash
cd deploy/terraform
terraform destroy      # type: yes
```

Everything created in step 6 is removed and the bill stops. The address is released; a
new server later gets a new one. Check the Lightsail console afterwards: it should show
no instances and no static IPs.

## What you are accepting

- **Plain HTTP.** The passcode and the cards travel unencrypted and the browser says
  "Not secure". Fine for a card game among friends; do not reuse a passcode that matters
  elsewhere. `docs/hosting.md` describes adding HTTPS later.
- **The passcode is stored in three places on your machine and AWS:** `terraform.tfvars`,
  the Terraform state file, and the instance's launch script. All stay out of git.
- **One server, in memory.** A restart ends the games in progress.
