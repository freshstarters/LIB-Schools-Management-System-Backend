from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("demo", "0004_staff_guardians_people"),
    ]

    operations = [
        migrations.AlterField(
            model_name="staffprofile",
            name="phone",
            field=models.CharField(blank=True, max_length=20),
        ),
    ]
