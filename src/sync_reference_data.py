"""Upload local PO and receipt CSVs to Supabase."""

from src.supabase_data import create_supabase_client, sync_reference_data_to_supabase


def main() -> None:
    counts = sync_reference_data_to_supabase(create_supabase_client())
    for table_name, count in counts.items():
        print(f"Upserted {count} row(s) into {table_name}")


if __name__ == "__main__":
    main()