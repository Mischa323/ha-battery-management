#!/usr/bin/env bash
# Provision the devcontainer: Home Assistant + test dependencies, and link the
# integration into the throwaway `config/` instance so an HA restart picks up
# code edits without copying anything.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> System packages that Home Assistant's default_config expects"
# Entirely best effort. HA only logs a warning when these are missing, and the
# base image ships third-party apt sources (yarn) whose signing key can be
# stale - that must never abort the rest of this script.
if sudo apt-get update -qq; then
  sudo apt-get install -y -qq ffmpeg libturbojpeg0 || true
else
  echo "    apt-get update failed; skipping (Home Assistant will still run)"
fi

echo "==> Python dependencies"
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt

echo "==> Linking the integration into config/custom_components"
mkdir -p config/custom_components
ln -sfn ../../custom_components/battery_management \
  config/custom_components/battery_management
# automation: !include needs the file to exist
touch config/automations.yaml
mkdir -p config/blueprints/automation
ln -sfn ../../../blueprints/automation/battery_management   config/blueprints/automation/battery_management

cat <<'EOF'

Ready.
  scripts/develop   start Home Assistant on http://localhost:8123
  scripts/test      run the pytest suite

First run: create a throwaway account at the onboarding screen, then
Settings -> Devices & Services -> Add Integration -> Battery Management.
The simulated grid meter and battery entities are already there (sim_*).
EOF
