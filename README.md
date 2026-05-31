# mihomo-cli

Standalone CLI controller for mihomo.

## Install

```bash
uv tool install mihomo-cli
```

Or with pip:

```bash
pip install mihomo-cli
```

## Configuration

Config directory: `~/.local/share/mihomo/`

| File | Description |
|------|-------------|
| `config.yaml` | Base config (ports, DNS, TUN) |
| `profiles.yaml` | Subscription list |
| `profiles/` | Downloaded subscription files |
| `script.js` | Global enhancement script (Node.js) |
| `generated.yaml` | Final generated config for mihomo |

## Usage

```bash
mihomo-ctl status          # Show mihomo status
mihomo-ctl groups         # List all proxy groups
mihomo-ctl list <group>   # List proxies in a group
mihomo-ctl switch <grp> <px>  # Switch selector group
mihomo-ctl gdelay <group> # Test group delay
mihomo-ctl sub            # Fetch subscriptions + apply + reload
mihomo-ctl apply          # Run script + generate config + reload
mihomo-ctl kernel         # Show/install kernel versions
```

## Options

| Flag | Description |
|------|-------------|
| `--verge` | Use Clash Verge's config directory instead of own |

## Examples

```bash
mihomo-ctl sub                    # fetch subs + apply + reload
mihomo-ctl --verge sub            # same but using Verge's config
mihomo-ctl groups
mihomo-ctl switch 'AI Suite' 'AI Auto'
mihomo-ctl gdelay Fast
mihomo-ctl kernel versions        # list recent releases
mihomo-ctl kernel upgrade         # upgrade to latest
```