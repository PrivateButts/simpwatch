from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("simpwatch", "0001_initial"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="identity",
            new_name="simpwatch_i_platfor_b8266f_idx",
            old_name="simpwatch_i_platfor_b2c0d4_idx",
        ),
        migrations.RenameIndex(
            model_name="scoreadjustment",
            new_name="simpwatch_s_created_52924e_idx",
            old_name="simpwatch_s_created_d80566_idx",
        ),
        migrations.RenameIndex(
            model_name="simpevent",
            new_name="simpwatch_s_created_6a84a5_idx",
            old_name="simpwatch_s_created_a5dd8e_idx",
        ),
        migrations.RenameIndex(
            model_name="simpevent",
            new_name="simpwatch_s_platfor_bbf9ed_idx",
            old_name="simpwatch_s_platfor_dfd114_idx",
        ),
        migrations.RenameIndex(
            model_name="simpevent",
            new_name="simpwatch_s_target__46807c_idx",
            old_name="simpwatch_s_target__db34f8_idx",
        ),
    ]
