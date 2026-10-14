# One small Lightsail server for the hosted Red Ten game. See docs/hosting.md.
#
#   terraform init
#   terraform apply      # prints the address to give the players
#   terraform destroy    # removes everything and stops the bill

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

locals {
  availability_zone = coalesce(var.availability_zone, "${var.region}a")

  # Runs once as root when the server first boots: settings first, then the
  # same install script used for updates.
  first_boot = join("\n", [
    "#!/bin/bash",
    "export REDTEN_PASSCODE='${var.passcode}'",
    "export REPO_URL='${var.repo_url}'",
    "export REPO_BRANCH='${var.repo_branch}'",
    file("${path.module}/../install.sh"),
  ])
}

resource "aws_lightsail_key_pair" "redten" {
  name       = "${var.name}-key"
  public_key = file(pathexpand(var.ssh_public_key_path))
}

resource "aws_lightsail_instance" "redten" {
  name              = var.name
  availability_zone = local.availability_zone
  blueprint_id      = var.blueprint_id
  bundle_id         = var.bundle_id
  key_pair_name     = aws_lightsail_key_pair.redten.name
  user_data         = local.first_boot
}

# A fixed address, so the one Mr. Liu hands out keeps working. Free while it
# is attached to the instance.
resource "aws_lightsail_static_ip" "redten" {
  name = "${var.name}-ip"
}

resource "aws_lightsail_static_ip_attachment" "redten" {
  static_ip_name = aws_lightsail_static_ip.redten.name
  instance_name  = aws_lightsail_instance.redten.name
}

# The game on port 80 for everyone; SSH only from the address you name.
resource "aws_lightsail_instance_public_ports" "redten" {
  instance_name = aws_lightsail_instance.redten.name

  port_info {
    protocol  = "tcp"
    from_port = 80
    to_port   = 80
    cidrs     = ["0.0.0.0/0"]
  }

  port_info {
    protocol  = "tcp"
    from_port = 22
    to_port   = 22
    cidrs     = [var.ssh_allowed_cidr]
  }
}
