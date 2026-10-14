output "address" {
  description = "Give this to the players, with the passcode."
  value       = "http://${aws_lightsail_static_ip.redten.ip_address}/"
}

output "ssh" {
  description = "How to log in to the server."
  value       = "ssh ubuntu@${aws_lightsail_static_ip.redten.ip_address}"
}
