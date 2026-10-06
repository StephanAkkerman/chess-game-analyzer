output "public_hostname" {
  description = "Public hostname exposed through the Cloudflare tunnel"
  value       = local.public_hostname
}

output "tunnel_id" {
  description = "Cloudflare tunnel ID"
  value       = cloudflare_zero_trust_tunnel_cloudflared.chess.id
}

output "tunnel_token" {
  description = "Token used by cloudflared to connect to the managed tunnel"
  value       = cloudflare_zero_trust_tunnel_cloudflared.chess.tunnel_token
  sensitive   = true
}

output "cloudflared_env_line" {
  description = "Env line to place in .env for the docker compose tunnel profile"
  value       = "CLOUDFLARE_TUNNEL_TOKEN=${cloudflare_zero_trust_tunnel_cloudflared.chess.tunnel_token}"
  sensitive   = true
}
