variable "passcode" {
  description = "The passcode every player types once to get in."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[A-Za-z0-9._-]{6,64}$", var.passcode))
    error_message = "Use 6-64 letters, digits, or - _ . (no spaces or other symbols)."
  }
}

variable "ssh_allowed_cidr" {
  description = "Who may SSH to the server: your own public address, like 203.0.113.7/32."
  type        = string

  validation {
    condition     = can(cidrhost(var.ssh_allowed_cidr, 0))
    error_message = "Give an address range such as 203.0.113.7/32."
  }
}

variable "ssh_public_key_path" {
  description = "Path to your SSH public key, installed on the server for the ubuntu user."
  type        = string
}

variable "region" {
  description = "AWS region, nearest the players."
  type        = string
  default     = "us-east-1"
}

variable "availability_zone" {
  description = "Zone inside the region. Leave empty for the region's first zone."
  type        = string
  default     = null
}

variable "bundle_id" {
  description = "Lightsail plan. nano_3_0 is the $5/month plan with 0.5 GB of memory and a public IPv4 address."
  type        = string
  default     = "nano_3_0"
}

variable "blueprint_id" {
  description = "Operating system image. install.sh expects Ubuntu."
  type        = string
  default     = "ubuntu_24_04"
}

variable "repo_url" {
  description = "Git repository the server installs the game from. Must be readable without a login."
  type        = string
  default     = "https://github.com/yszhangit/red-ten.git"
}

variable "repo_branch" {
  description = "Branch to install."
  type        = string
  default     = "hosted-play"
}

variable "name" {
  description = "Name for the Lightsail resources."
  type        = string
  default     = "redten"
}
