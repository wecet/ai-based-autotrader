# Documentation

These guides describe the implementation in this repository. Configuration and
model defaults are distinguished from broker account settings and live results.

| Guide | Contents |
| --- | --- |
| [Live settings dashboard](dashboard.md) | Realtime controls, access key, and settings persistence |
| [Drop-in strategies](models.md) | Model interface, discovery, metadata, and runtime selection |
| [Configuration](configuration.md) | Required credentials, environment defaults, and JSON universe format |
| [Universe](universe.md) | The 50-ETF / 100-stock selection and benchmark mapping |
| [Trading behavior](trading.md) | Feature generation, training, allocation, and execution |
| [Deployment and operations](operations.md) | Start, stop, update, health checks, state, and errors |
| [Development and testing](development.md) | Source layout, offline tests, VS Code, and conventions |

For a first deployment, read configuration and universe before starting the
container. For a service that is running without placing orders, use the
trading behavior and operations guides.

Return to the [project README](../README.md).
