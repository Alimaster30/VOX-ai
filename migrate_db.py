from src.persistence import init_db, migration_status


def main() -> None:
    before = migration_status()
    init_db()
    after = migration_status()

    applied_now = [version for version in after["applied"] if version not in set(before["applied"])]
    print(f"Database backend: {after['backend']}")
    print(f"Available migrations: {len(after['available'])}")
    print(f"Applied migrations: {len(after['applied'])}")
    print(f"Pending migrations: {len(after['pending'])}")
    if applied_now:
        print("Applied now:")
        for version in applied_now:
            print(f"- {version}")
    else:
        print("Applied now: none")


if __name__ == "__main__":
    main()
