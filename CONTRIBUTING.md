# Contributing

Thank you for considering a contribution to this project!

## Ways to contribute

- **Bug reports** — open an [issue](https://github.com/MarcinG81/SunSynk_HA_Integration/issues/new/choose) using the bug report template
- **Feature requests** — open an [issue](https://github.com/MarcinG81/SunSynk_HA_Integration/issues/new/choose) using the feature request template
- **Discussions** — ask questions or share ideas in [Discussions](https://github.com/MarcinG81/SunSynk_HA_Integration/discussions)
- **Pull requests** — see below

## Pull requests

1. Fork the repository and create a branch from `main`
2. Make your changes in `custom_components/sunsynk/`
3. Test against a real Home Assistant instance with a real or test inverter
4. Update `CHANGELOG.md` under an `[Unreleased]` section
5. Open a PR — fill in the PR template

## Development setup

```bash
git clone https://github.com/<your-fork>/SunSynk_HA_Integration.git
cd SunSynk_HA_Integration
```

Copy the `custom_components/sunsynk/` folder into your HA `config/custom_components/` directory and restart HA.

For validation, the repository uses GitHub Actions running **hassfest** and **HACS validation** on every push and PR.

## Code style

- Follow standard Python / Home Assistant integration conventions
- No external dependencies beyond what is already in `manifest.json`
- Keep entity names short — `has_entity_name = True` prepends the device name automatically

## Commit messages

Use the conventional commits format:

```
feat: short description
fix: short description
docs: short description
```
