# Documentation

These guides describe the implementation in this repository. Configuration and
model defaults are distinguished from broker account settings and live results.

| Guide | Contents |
| --- | --- |
| [Dashboard and performance calendar](dashboard.md) | Live controls, access key, daily P/L, trade fills, and reporting freshness |
| [Drop-in strategies](models.md) | Model interface, discovery, metadata, and runtime selection |
| [Configuration](configuration.md) | Required credentials, environment defaults, and JSON universe format |
| [Universe](universe.md) | Versioned 230-candidate catalog, additive updates, commodity funds and benchmark mapping |
| [Strategy research](research.md) | Momentum and boosted ranking, offline evaluation, final-test isolation and research roadmap |
| [Trading behavior](trading.md) | Feature generation, training, allocation, and execution |
| [Deployment and operations](operations.md) | Start, stop, update, health checks, state, and errors |
| [Development and testing](development.md) | Source layout, offline tests, VS Code, and conventions |

For a first deployment, read configuration and universe before starting the
container. For a service that is running without placing orders, use the
trading behavior and operations guides. For daily results and completed trade
fills, use the performance calendar in the dashboard. Optional development
submodules and clone instructions are documented in [libs/README.md](../libs/README.md).

Return to the [project README](../README.md).
