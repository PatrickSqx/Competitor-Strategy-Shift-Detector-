from app.adapters.neo4j_store import Neo4jStore
from app.config import get_settings


def main() -> None:
    settings = get_settings()
    store = Neo4jStore(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
    store.ensure_schema()
    store.seed_targets(settings.scrape_targets)
    print("Seed completed. Neo4j enabled:", store.enabled)
    store.close()


if __name__ == "__main__":
    main()
