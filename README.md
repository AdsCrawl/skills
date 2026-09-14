# AdsCrawl skills

Webpage content extraction, PNG screenshots, temporary remote CDP control, and persistent cloud browser profiles with explicit custom-proxy start and confirmed stop.

The skill entry point is [`skills/SKILL.md`](skills/SKILL.md), named `adscrawl-browser`. Install/package the complete `skills/` directory so its references and Python helper remain available. See the [persistent lifecycle reference](skills/references/cloud-browsers.md) for authentication, quotas, errors, and a complete example.

## Development

The lifecycle helper uses Python 3.9+ and the standard library. Run its HTTP mock tests from the repository root:

```bash
python3 -m unittest discover -s tests -v
```

Tests use fake credentials and loopback HTTP servers. Real browser, proxy egress, billing, and production quota verification require a separate test environment.

This repository has no version manifest or automatic publication workflow. Submit source changes through a PR; marketplace publication is a separate action. Packaging must retain `SKILL.md`, `references/`, and `scripts/` together.
