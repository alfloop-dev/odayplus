mock_provider "google" {
  mock_data "google_compute_network" {
    defaults = { name = "default" }
  }
  mock_data "google_vpc_access_connector" {
    defaults = {
      network       = "default"
      ip_cidr_range = "10.8.0.0/28"
      state         = "READY"
    }
  }
}

run "default_noop" {
  command = plan
  assert {
    condition     = length(google_compute_firewall.deny) == 0 && length(google_compute_firewall.sql) == 0 && length(google_compute_firewall.google_https) == 0 && length(google_dns_managed_zone.private) == 0 && length(terraform_data.binding) == 0 && length(data.google_vpc_access_connector.existing) == 0
    error_message = "Default invocation must own/create nothing and avoid cloud lookups."
  }
}

run "shared_dns_only" {
  command = plan
  variables {
    enable_shared_dns     = true
    shared_dns_scope_ack  = "default:googleapis.com.,run.app."
    shared_dns_review_ref = "ODP-DEV-TEST:offline-only"
  }
  assert {
    condition     = length(google_compute_firewall.deny) == 0 && length(google_dns_managed_zone.private) == 2
    error_message = "Prepare separately authorized DNS before installing deny."
  }
}

run "scoped_protection" {
  command = plan
  variables {
    enable_firewall                  = true
    enable_shared_dns                = true
    shared_dns_scope_ack             = "default:googleapis.com.,run.app."
    shared_dns_review_ref            = "ODP-DEV-TEST:offline-only"
    connector_tag_readback_confirmed = true
  }
  assert {
    condition = alltrue([
      for rule in [google_compute_firewall.deny[0], google_compute_firewall.sql[0], google_compute_firewall.google_https[0]] :
      rule.network == "https://www.googleapis.com/compute/v1/projects/odayplus-runtime-20260825/global/networks/default" &&
      rule.direction == "EGRESS" && rule.target_tags == toset(["vpc-connector-asia-east1-oday-staging-vpc"]) && rule.priority > 100
    ])
    error_message = "Every firewall must target only the documented unique connector tag on the EXISTING default network, leaving managed priority 100 intact."
  }
  assert {
    condition     = google_compute_firewall.deny[0].destination_ranges == toset(["0.0.0.0/0"]) && one(google_compute_firewall.deny[0].deny).protocol == "all" && google_compute_firewall.deny[0].priority == 900 && length(google_compute_firewall.deny[0].allow) == 0
    error_message = "Require all-protocol IPv4 default-deny, never an internet allow."
  }
  assert {
    condition     = google_compute_firewall.google_https[0].destination_ranges == toset(["199.36.153.8/30"]) && one(google_compute_firewall.google_https[0].allow).protocol == "tcp" && toset(one(google_compute_firewall.google_https[0].allow).ports) == toset(["443"]) && google_compute_firewall.google_https[0].priority < google_compute_firewall.deny[0].priority
    error_message = "Web/API, Google APIs, audit/storage HTTPS must use only the reviewed private VIP."
  }
  assert {
    condition     = google_compute_firewall.sql[0].destination_ranges == toset(["10.50.0.3/32"]) && one(google_compute_firewall.sql[0].allow).protocol == "tcp" && toset(one(google_compute_firewall.sql[0].allow).ports) == toset(["5432", "3307"]) && google_compute_firewall.sql[0].priority < google_compute_firewall.deny[0].priority
    error_message = "Private PostgreSQL and Auth Proxy may reach only exact SQL /32."
  }
  assert {
    condition     = alltrue([for rule in [google_compute_firewall.sql[0], google_compute_firewall.google_https[0]] : !contains(rule.destination_ranges, "0.0.0.0/0") && rule.log_config[0].metadata == "INCLUDE_ALL_METADATA"])
    error_message = "No broad internet allow; log permitted destinations."
  }
  assert {
    condition     = google_dns_managed_zone.private["googleapis"].dns_name == "googleapis.com." && google_dns_managed_zone.private["run"].dns_name == "run.app." && alltrue([for zone in google_dns_managed_zone.private : zone.visibility == "private" && one(zone.private_visibility_config[0].networks).network_url == "https://www.googleapis.com/compute/v1/projects/odayplus-runtime-20260825/global/networks/default"])
    error_message = "DNS is explicitly shared-default scope, never on staging-runtime or connector-local."
  }
  assert {
    condition     = google_dns_record_set.vip["googleapis"].name == "private.googleapis.com." && google_dns_record_set.vip["run"].name == "run.app." && alltrue([for record in google_dns_record_set.vip : record.type == "A" && record.rrdatas == tolist(["199.36.153.8", "199.36.153.9", "199.36.153.10", "199.36.153.11"])]) && google_dns_record_set.wildcard["googleapis"].rrdatas == tolist(["private.googleapis.com."]) && google_dns_record_set.wildcard["run"].rrdatas == tolist(["run.app."]) && alltrue([for record in google_dns_record_set.wildcard : record.type == "CNAME"])
    error_message = "Both required wildcard namespaces must resolve to the same supported private VIP."
  }
}

run "reject_staging" {
  command = plan
  variables { environment = "staging" }
  expect_failures = [var.environment]
}
run "reject_production" {
  command = plan
  variables { environment = "production" }
  expect_failures = [var.environment]
}
run "reject_wrong_project" {
  command = plan
  variables { project_id = "other-production" }
  expect_failures = [var.project_id]
}
run "reject_wrong_region" {
  command = plan
  variables { region = "us-central1" }
  expect_failures = [var.region]
}
run "reject_wrong_network" {
  command = plan
  variables { network_name = "oday-staging-runtime" }
  expect_failures = [var.network_name]
}
run "reject_connector_uri" {
  command = plan
  variables { connector_name = "projects/odayplus-runtime-20260825/locations/asia-east1/connectors/oday-staging-vpc" }
  expect_failures = [var.connector_name]
}
run "reject_other_connector" {
  command = plan
  variables { connector_name = "production-vpc" }
  expect_failures = [var.connector_name]
}
run "reject_widened_connector_cidr" {
  command = plan
  variables { connector_cidr = "10.8.0.0/24" }
  expect_failures = [var.connector_cidr]
}
run "reject_malformed_sql" {
  command = plan
  variables { sql_private_cidr = "not-a-cidr" }
  expect_failures = [var.sql_private_cidr]
}
run "reject_broad_sql" {
  command = plan
  variables { sql_private_cidr = "0.0.0.0/0" }
  expect_failures = [var.sql_private_cidr]
}
run "reject_unreviewed_dns" {
  command = plan
  variables { enable_shared_dns = true }
  expect_failures = [terraform_data.binding]
}
run "reject_connector_local_dns_claim" {
  command = plan
  variables {
    enable_shared_dns     = true
    shared_dns_scope_ack  = "connector-local"
    shared_dns_review_ref = "ODP-DEV-TEST:offline-only"
  }
  expect_failures = [terraform_data.binding]
}
run "reject_firewall_without_dns" {
  command = plan
  variables {
    enable_firewall                  = true
    connector_tag_readback_confirmed = true
  }
  expect_failures = [terraform_data.binding]
}
run "reject_unconfirmed_target_tag" {
  command = plan
  variables {
    enable_firewall       = true
    enable_shared_dns     = true
    shared_dns_scope_ack  = "default:googleapis.com.,run.app."
    shared_dns_review_ref = "ODP-DEV-TEST:offline-only"
  }
  expect_failures = [terraform_data.binding]
}
run "reject_live_connector_mismatch" {
  command = plan
  variables {
    enable_shared_dns     = true
    shared_dns_scope_ack  = "default:googleapis.com.,run.app."
    shared_dns_review_ref = "ODP-DEV-TEST:offline-only"
  }
  override_data {
    target = data.google_vpc_access_connector.existing[0]
    values = {
      network       = "oday-staging-runtime"
      ip_cidr_range = "10.8.0.0/28"
      state         = "READY"
    }
  }
  expect_failures = [terraform_data.binding]
}
run "reject_live_cidr_drift" {
  command = plan
  variables {
    enable_shared_dns     = true
    shared_dns_scope_ack  = "default:googleapis.com.,run.app."
    shared_dns_review_ref = "ODP-DEV-TEST:offline-only"
  }
  override_data {
    target = data.google_vpc_access_connector.existing[0]
    values = {
      network       = "default"
      ip_cidr_range = "10.9.0.0/28"
      state         = "READY"
    }
  }
  expect_failures = [terraform_data.binding]
}
run "reject_not_ready" {
  command = plan
  variables {
    enable_shared_dns     = true
    shared_dns_scope_ack  = "default:googleapis.com.,run.app."
    shared_dns_review_ref = "ODP-DEV-TEST:offline-only"
  }
  override_data {
    target = data.google_vpc_access_connector.existing[0]
    values = {
      network       = "default"
      ip_cidr_range = "10.8.0.0/28"
      state         = "CREATING"
    }
  }
  expect_failures = [terraform_data.binding]
}
