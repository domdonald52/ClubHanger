import re
from django.core.management.base import BaseCommand
from core.models import AircraftMaintenanceItem, MaintenanceType


def decode_js_escapes(s):
    """Replace literal \\uXXXX sequences (from escapejs stored in data attrs) with real chars."""
    if not s:
        return s
    return re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), s)


class Command(BaseCommand):
    help = 'Fix maintenance item/type names that were stored with literal JS escape sequences'

    def handle(self, *args, **options):
        fixed = 0
        for item in AircraftMaintenanceItem.objects.all():
            new_name = decode_js_escapes(item.name)
            new_desc = decode_js_escapes(item.description)
            if new_name != item.name or new_desc != item.description:
                self.stdout.write(f'  {item.name!r} → {new_name!r}')
                item.name = new_name
                item.description = new_desc
                item.save(update_fields=['name', 'description'])
                fixed += 1

        for mt in MaintenanceType.objects.all():
            new_name = decode_js_escapes(mt.name)
            new_desc = decode_js_escapes(mt.description)
            if new_name != mt.name or new_desc != mt.description:
                self.stdout.write(f'  Type: {mt.name!r} → {new_name!r}')
                mt.name = new_name
                mt.description = new_desc
                mt.save(update_fields=['name', 'description'])
                fixed += 1

        self.stdout.write(self.style.SUCCESS(f'Fixed {fixed} record(s).'))
