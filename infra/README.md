# Cloudflare IaC (Terraform)

Provisions the Cloudflare side of the `tunnel` Compose profile, so you don't have to click
through the dashboard: a managed tunnel, its ingress config and the DNS record.

## Resources Created

- Cloudflare tunnel (`cloudflare_zero_trust_tunnel_cloudflared`)
- Tunnel ingress config routing `<subdomain>.<zone_name>` to `http://app:8000`
- DNS CNAME record: `<subdomain>.<zone_name>` -> `<tunnel-id>.cfargotunnel.com`
- *Optional*: a Cloudflare Access application + allow policy, only when
  `access_allowed_emails` is non-empty.

## Prerequisites

- Domain delegated to Cloudflare nameservers
- Cloudflare API token with:
  - Account: Cloudflare Tunnel Edit
  - Zone: DNS Edit
  - Zone: Zone Read
  - Account: Access: Apps and Policies Edit (only if you use the Access gate)
- Terraform >= 1.5

## Quick Run

1. `cp terraform.tfvars.example terraform.tfvars` and fill in the token and account ID.
2. `terraform init && terraform apply`
3. `terraform output -raw tunnel_token`
4. In the repo root `.env`, set `COMPOSE_PROFILES=tunnel` and
   `CLOUDFLARE_TUNNEL_TOKEN=<token>`, then `docker compose up -d`.

## Notes

- `terraform.tfvars` and state files are git-ignored; the state contains the tunnel secret,
  so keep it private.
- The app has its own `ACCESS_CODE`, and finished analyses are viewable by anyone with a
  link. The Access gate is therefore off by default; enabling it protects the whole site but
  also locks out people you share analysis links with.
