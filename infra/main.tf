terraform {
  required_version = ">= 1.5.0"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}

locals {
  public_hostname = "${var.subdomain}.${var.zone_name}"
  use_access      = length(var.access_allowed_emails) > 0
}

data "cloudflare_zone" "zone" {
  name = var.zone_name
}

resource "random_bytes" "tunnel_secret" {
  length = 32
}

resource "cloudflare_zero_trust_tunnel_cloudflared" "chess" {
  account_id = var.cloudflare_account_id
  name       = var.tunnel_name
  secret     = random_bytes.tunnel_secret.base64
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "chess" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.chess.id

  config {
    ingress_rule {
      hostname = local.public_hostname
      service  = var.tunnel_origin_url
    }

    ingress_rule {
      service = "http_status:404"
    }
  }
}

resource "cloudflare_record" "chess" {
  zone_id = data.cloudflare_zone.zone.id
  name    = var.subdomain
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.chess.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
}

# Optional: gates the whole hostname behind Cloudflare Access (email one-time
# PIN). Off by default, because the app has its own ACCESS_CODE and finished
# analyses are meant to be viewable by anyone with a link; an Access gate
# would block those links too. Set access_allowed_emails to turn it on.
resource "cloudflare_zero_trust_access_application" "chess" {
  count = local.use_access ? 1 : 0

  account_id       = var.cloudflare_account_id
  name             = "chess-game-analyzer"
  domain           = local.public_hostname
  type             = "self_hosted"
  session_duration = var.access_session_duration
}

resource "cloudflare_zero_trust_access_policy" "chess_allowed_users" {
  count = local.use_access ? 1 : 0

  account_id     = var.cloudflare_account_id
  application_id = cloudflare_zero_trust_access_application.chess[0].id
  name           = "Allowed users"
  precedence     = 1
  decision       = "allow"

  include {
    email = var.access_allowed_emails
  }
}
