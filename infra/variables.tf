variable "cloudflare_api_token" {
  description = "Cloudflare API token with Zone DNS + Zero Trust Tunnel (and, if used, Access) permissions"
  type        = string
  sensitive   = true
}

variable "cloudflare_account_id" {
  description = "Cloudflare account ID"
  type        = string
}

variable "zone_name" {
  description = "Cloudflare zone name (your own domain, delegated to Cloudflare nameservers)"
  type        = string
}

variable "subdomain" {
  description = "Subdomain for the public app hostname"
  type        = string
  default     = "chess"
}

variable "tunnel_name" {
  description = "Name for the Cloudflare tunnel"
  type        = string
  default     = "chess-pi"
}

variable "tunnel_origin_url" {
  description = "Origin URL cloudflared should route traffic to"
  type        = string
  default     = "http://app:8000"
}

variable "access_allowed_emails" {
  description = "Emails allowed through Cloudflare Access (one-time PIN). Empty (default) means no Access gate; the app's ACCESS_CODE is the only protection."
  type        = list(string)
  default     = []
}

variable "access_session_duration" {
  description = "How long an Access login stays valid before re-authenticating"
  type        = string
  default     = "24h"
}
