from django.db import migrations

OLD_CONSTRAINT = "django_admin_log_user_id_c564eba6_fk_auth_user_id"
NEW_CONSTRAINT = "django_admin_log_user_id_fk_core_user"


def repair_admin_log(apps, schema_editor):
    """Point an already-applied admin log at the custom user.

    A database created after this user model exists already stores
    ``user_id`` as uuid. A database created earlier still has an integer
    column aimed at ``auth_user``.
    """
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'django_admin_log'
              AND column_name = 'user_id'
            """
        )
        row = cursor.fetchone()
        if row is None or row[0] == "uuid":
            return
        cursor.execute("DELETE FROM django_admin_log")
        cursor.execute(
            f"ALTER TABLE django_admin_log DROP CONSTRAINT IF EXISTS {OLD_CONSTRAINT}"
        )
        cursor.execute(
            "ALTER TABLE django_admin_log ALTER COLUMN user_id TYPE uuid USING NULL"
        )
        cursor.execute(
            f"""
            ALTER TABLE django_admin_log
            ADD CONSTRAINT {NEW_CONSTRAINT}
            FOREIGN KEY (user_id) REFERENCES core_user (id)
            DEFERRABLE INITIALLY DEFERRED
            """
        )


class Migration(migrations.Migration):
    dependencies = [
        ("admin", "0001_initial"),
        ("core", "0003_user_and_refresh_token"),
    ]

    operations = [
        migrations.RunPython(repair_admin_log, migrations.RunPython.noop),
    ]
