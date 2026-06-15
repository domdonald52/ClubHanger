from django.db import models
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.utils.text import slugify
from datetime import date, datetime, time, timedelta


# ============================================================================
# AUTH & ORGANIZATION
# ============================================================================

class User(AbstractUser):
    """
    Authentication only. Personal details live on ClubMember.
    Inherits: username, email, password, first_name, last_name, is_active, date_joined, etc.
    """
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['last_name', 'first_name']

    def __str__(self):
        return f"{self.get_full_name()} ({self.username})"


class Club(models.Model):
    """
    Top-level organization. Each club manages its own members, aircraft, bookings.
    """
    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(unique=True)
    
    # Contact & operational
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    
    # Operational settings
    timezone = models.CharField(max_length=50, default='Pacific/Auckland')
    currency = models.CharField(max_length=3, default='NZD')
    
    # Membership
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        ordering = ['name']
    
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class ClubConfig(models.Model):
    """Club operational configuration."""
    club = models.OneToOneField(Club, on_delete=models.CASCADE, related_name='config')
    
    # Booking defaults
    default_booking_duration = models.IntegerField(default=90, help_text="Minutes")
    time_slot_interval = models.IntegerField(default=30, help_text="Minutes")
    duration_options = models.CharField(
        max_length=200, default="60,90,120,180",
        help_text="Comma-separated minutes offered in the booking/search duration picker"
    )

    # Explicit booking slots — HH:MM-HH:MM pairs defining the standard booking windows.
    # Replaces the old typical_hours_start/end approach with fully configurable slot definitions.
    # If blank, falls back to computing from default_booking_duration + time_slot_interval + operating hours.
    booking_slots = models.TextField(
        blank=True, default='',
        help_text="Comma-separated HH:MM-HH:MM slot pairs, e.g. 08:30-10:00, 10:00-11:30, 12:30-14:00"
    )

    def duration_choices(self):
        """Parsed, sorted, de-duplicated list of duration options (ints)."""
        out = []
        for part in (self.duration_options or "").split(","):
            part = part.strip()
            if part.isdigit():
                out.append(int(part))
        if self.default_booking_duration not in out:
            out.append(self.default_booking_duration)
        return sorted(set(out))

    def parsed_booking_slots(self):
        """Return list of (start_str, end_str, sh, sm, eh, em) from booking_slots text."""
        result = []
        for part in (self.booking_slots or '').split(','):
            part = part.strip()
            if not part or '-' not in part:
                continue
            try:
                start, end = part.split('-', 1)
                start, end = start.strip(), end.strip()
                sh, sm = int(start[0:2]), int(start[3:5])
                eh, em = int(end[0:2]),   int(end[3:5])
                result.append((start, end, sh, sm, eh, em))
            except (ValueError, IndexError):
                pass
        return result

    def slot_window(self):
        """Returns (start_time, end_time) for the standard booking window.
        Uses first/last slot if defined, otherwise falls back to typical_hours_start/end."""
        from datetime import time as _t
        slots = self.parsed_booking_slots()
        if slots:
            return _t(slots[0][2], slots[0][3]), _t(slots[-1][4], slots[-1][5])
        return self.typical_hours_start, self.typical_hours_end

    # Operating hours (the full window the calendar spans)
    operating_hours_start = models.TimeField(default='07:00')
    operating_hours_end = models.TimeField(default='21:00')

    # Typical flying hours (bookings outside this range require confirmation)
    typical_hours_start = models.TimeField(default='08:30')
    typical_hours_end = models.TimeField(default='17:00')

    # Cancellation fees
    cancellation_notice_hours = models.PositiveIntegerField(
        default=24,
        help_text="Bookings cancelled within this many hours of the start time may incur a fee"
    )
    cancellation_fee_amount = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        help_text="Fee charged for late cancellations. Set to 0 to disable automatic prompting."
    )

    # Club branding
    logo = models.ImageField(upload_to='logos/', null=True, blank=True,
                             help_text="Club logo — ideally white on transparent PNG, shown in the banner")

    # Theme colours (hex). Aviation palette.
    theme_banner = models.CharField(max_length=7, default='#2b2b2b', help_text="Top banner background")
    theme_primary = models.CharField(max_length=7, default='#c0481c', help_text="Buttons, links, active controls")
    theme_accent = models.CharField(max_length=7, default='#d4732a', help_text="Accent / highlights")
    theme_confirmed = models.CharField(max_length=7, default='#16a34a', help_text="Confirmed booking pills")
    theme_pending = models.CharField(max_length=7, default='#e8a800', help_text="Pending booking pills")
    theme_departed = models.CharField(max_length=7, default='#d97706', help_text="Departed booking pills")
    theme_returned = models.CharField(max_length=7, default='#2563eb', help_text="Returned (awaiting payment) booking pills")
    theme_completed_paid = models.CharField(max_length=7, default='#7c3aed', help_text="Completed & paid booking pills")
    theme_weekend = models.CharField(max_length=7, default='#fdf0e6', help_text="Weekend shading in search")
    theme_atypical = models.CharField(max_length=7, default='#f0f0f0', help_text="Outside-typical-hours shading")
    chart_colors = models.JSONField(
        default=list,
        help_text="Ordered list of hex colours used in charts. Should have 8–10 distinct colours that complement the primary theme."
    )

    FONT_SYSTEM  = 'system'
    FONT_INTER   = 'inter'
    FONT_LORA    = 'lora'
    FONT_POPPINS = 'poppins'
    FONT_NUNITO  = 'nunito'
    FONT_CHOICES = [
        (FONT_SYSTEM,  'System default'),
        (FONT_INTER,   'Inter (modern sans-serif)'),
        (FONT_POPPINS, 'Poppins (geometric sans-serif)'),
        (FONT_NUNITO,  'Nunito (rounded, friendly)'),
        (FONT_LORA,    'Lora (elegant serif)'),
    ]
    font_choice = models.CharField(
        max_length=20, default=FONT_SYSTEM, choices=FONT_CHOICES,
        help_text="Body font used across all pages."
    )

    FONT_STACKS = {
        FONT_SYSTEM:  ("system-ui,-apple-system,'Segoe UI',Helvetica,Arial,sans-serif", None),
        FONT_INTER:   ("'Inter',sans-serif",   "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap"),
        FONT_POPPINS: ("'Poppins',sans-serif", "https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600&display=swap"),
        FONT_NUNITO:  ("'Nunito',sans-serif",  "https://fonts.googleapis.com/css2?family=Nunito:wght@400;500;700&display=swap"),
        FONT_LORA:    ("'Lora',Georgia,serif", "https://fonts.googleapis.com/css2?family=Lora:wght@400;500;600&display=swap"),
    }

    def get_font(self):
        return self.FONT_STACKS.get(self.font_choice, self.FONT_STACKS[self.FONT_SYSTEM])

    _DEFAULT_CHART_COLORS = [
        '#ae3708',  # Rust — burnt orange-red
        '#173d3c',  # Tiber — deep dark teal
        '#f89d08',  # Orange Peel — warm amber
        '#e64301',  # Persimmon — red-orange
        '#7d7268',  # warm taupe-grey
        '#fb8007',  # Tangerine — bright orange
        '#c4a882',  # warm sand
        '#4a4540',  # dark warm charcoal
        '#d4824a',  # mid burnt orange
        '#9b8b7a',  # medium warm taupe
    ]

    def get_chart_colors(self):
        """Return chart palette, falling back to the built-in burnt-orange defaults."""
        colors = self.chart_colors
        if not colors or not isinstance(colors, list):
            return self._DEFAULT_CHART_COLORS
        # Pad with defaults if fewer than 4 defined
        if len(colors) < 4:
            colors = colors + self._DEFAULT_CHART_COLORS[len(colors):]
        return colors

    # ── Compliance / eligibility ─────────────────────────────────────────────
    bfr_interval_months   = models.PositiveIntegerField(default=24,
                                                         help_text="Flight Review required every N months (club policy)")
    medical_warning_days  = models.PositiveIntegerField(default=30,
                                                         help_text="Warn when a medical expires within this many days")
    recency_warning_days  = models.PositiveIntegerField(default=90,
                                                         help_text="Warn when a member hasn't flown an aircraft type in this many days (solo/private flights)")
    # Medical validity periods (months) — editable so regulatory changes don't require a code deploy
    medical_class1_under40 = models.PositiveIntegerField(default=12)
    medical_class1_over40  = models.PositiveIntegerField(default=6)
    medical_class2_under40 = models.PositiveIntegerField(default=24)
    medical_class2_over40  = models.PositiveIntegerField(default=12)
    medical_class3_under40 = models.PositiveIntegerField(default=60)
    medical_class3_over40  = models.PositiveIntegerField(default=24)

    # ── Billing ──────────────────────────────────────────────────────────────
    billing_name    = models.CharField(max_length=200, blank=True,
                                       help_text="Legal name on invoices, e.g. 'Wellington Aero Club Inc.'")
    billing_address = models.TextField(blank=True, help_text="Postal address printed on invoices")
    billing_phone   = models.CharField(max_length=30, blank=True)
    billing_email   = models.EmailField(blank=True)
    gst_number      = models.CharField(max_length=30, blank=True, help_text="GST / tax registration number")
    gst_rate        = models.DecimalField(max_digits=5, decimal_places=2, default=15,
                                          help_text="GST rate as a percentage, e.g. 15 for 15%")
    bank_name       = models.CharField(max_length=100, blank=True)
    bank_account    = models.CharField(max_length=30, blank=True)
    payment_terms_days = models.PositiveIntegerField(default=14,
                                                      help_text="Days from invoice date until payment is due")
    payment_terms_text = models.TextField(blank=True,
                                          help_text="Payment instructions printed on invoice footer")
    invoice_number_next   = models.PositiveIntegerField(default=1,
                                                         help_text="Next invoice number to allocate — set this to continue from your current sequence")
    invoice_number_prefix = models.CharField(max_length=10, blank=True,
                                              help_text="Optional prefix, e.g. 'INV-'")

    fy_start_month = models.PositiveSmallIntegerField(
        default=4, help_text="Month the financial year starts (1=Jan … 12=Dec). NZ default: April (4)")

    lapse_grace_days = models.PositiveIntegerField(
        default=60,
        help_text="Days after subscription_expires before a member is auto-lapsed. "
                  "Run manage.py update_lapsed_members (or use Settings) to apply."
    )

    # ── Booking blocks (financial) ────────────────────────────────────────────
    booking_block_enabled = models.BooleanField(
        default=False,
        help_text="Enable financial booking blocks. Individual conditions below must also be configured."
    )
    booking_block_credit_limit = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text="Block when account balance goes below negative this amount (e.g. 200 = block at −$200). Leave blank to disable."
    )
    booking_block_unpaid_flight_days = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Block when a flight charge has been unpaid for more than this many days. Leave blank to disable."
    )
    booking_block_invoice_days = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Block when an invoice is older than this many days and still unpaid. Leave blank to disable."
    )
    booking_block_message = models.TextField(
        blank=True,
        help_text="Message shown to a blocked member. If blank, auto-generated from billing phone/email."
    )

    # ── Maintenance alert thresholds (defaults; can be overridden per item) ────
    maint_warn_hours  = models.DecimalField(max_digits=6, decimal_places=1, default=20,
        help_text="Default: go amber when this many maintenance hours remain")
    maint_alert_hours = models.DecimalField(max_digits=6, decimal_places=1, default=5,
        help_text="Default: go red when this many maintenance hours remain")
    maint_warn_days   = models.PositiveIntegerField(default=30,
        help_text="Default: go amber when this many days remain until a date-based item is due")
    maint_alert_days  = models.PositiveIntegerField(default=14,
        help_text="Default: go red when this many days remain until a date-based item is due")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.club.name} Config"


class Role(models.Model):
    """Extensible club roles with a configurable permission matrix."""
    BOOKINGS_NONE       = 'none'
    BOOKINGS_VIEW_OWN   = 'view_own'
    BOOKINGS_MANAGE_OWN = 'manage_own'
    BOOKINGS_MANAGE_ALL = 'manage_all'
    BOOKINGS_CHOICES = [
        (BOOKINGS_NONE,       'No booking access'),
        (BOOKINGS_VIEW_OWN,   'View own bookings only'),
        (BOOKINGS_MANAGE_OWN, 'Manage own bookings'),
        (BOOKINGS_MANAGE_ALL, 'Manage all bookings'),
    ]

    SYSTEM_MEMBER     = 'member'
    SYSTEM_INSTRUCTOR = 'instructor'
    SYSTEM_ADMIN      = 'admin'
    SYSTEM_ROLE_CHOICES = [
        (SYSTEM_MEMBER,     'Member'),
        (SYSTEM_INSTRUCTOR, 'Instructor'),
        (SYSTEM_ADMIN,      'Admin'),
    ]

    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='roles')
    name = models.CharField(max_length=50)
    system_role_type = models.CharField(
        max_length=20, choices=SYSTEM_ROLE_CHOICES, blank=True, default='',
        help_text="Set for the three built-in roles (member/instructor/admin). Permissions are locked."
    )

    # Permission matrix
    bookings_access    = models.CharField(max_length=20, choices=BOOKINGS_CHOICES, default=BOOKINGS_VIEW_OWN)
    can_access_manage  = models.BooleanField(default=False, help_text="Access to Operations and People (bookings, members, block-outs, sales)")
    can_access_fleet   = models.BooleanField(default=False, help_text="Access to Fleet section (aircraft management)")
    can_access_safety  = models.BooleanField(default=False, help_text="Access to Safety section (events review, action items)")
    can_access_settings = models.BooleanField(default=False, help_text="Access to Configuration (settings, types)")
    can_access_reports  = models.BooleanField(default=False, help_text="Access to Analytics and Reports")
    is_superadmin       = models.BooleanField(default=False, help_text="Full access to everything — all permissions implied")

    # Membership renewal
    renewal_required    = models.BooleanField(default=True, help_text="Members with this role are included in the annual renewal cycle")
    annual_renewal_fee  = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True,
                                              help_text="Annual fee for this role. Leave blank if not applicable.")

    class Meta:
        unique_together = ('club', 'name')

    def __str__(self):
        return self.name

    @property
    def is_system_role(self):
        return bool(self.system_role_type)

    @property
    def effective_is_admin(self):
        return self.is_superadmin or self.can_access_settings

    @property
    def effective_is_instructor(self):
        if self.system_role_type:
            return self.system_role_type == self.SYSTEM_INSTRUCTOR
        return self.bookings_access == self.BOOKINGS_MANAGE_ALL and self.can_access_manage


class MembershipCategory(models.Model):
    """Extensible membership categories per club."""
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='membership_categories')
    name = models.CharField(max_length=100)  # 'Private Pilot', 'Student', etc.
    is_member = models.BooleanField(default=True, help_text="True=member, False=non-member")

    class Meta:
        unique_together = ('club', 'name')
        verbose_name_plural = "Membership categories"

    def __str__(self):
        status = "Member" if self.is_member else "Non-Member"
        return f"{self.club.name} - {self.name} ({status})"


class InstructorGrade(models.Model):
    """
    Instructor qualification grades per club (C-Cat, B-Cat, A-Cat, Examiner, etc.).
    Determines the hourly rate charged when an instructor flies dual.
    """
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='instructor_grades')
    name = models.CharField(max_length=50, help_text="e.g. C-Cat, B-Cat, A-Cat, Examiner")
    hourly_rate = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    display_order = models.PositiveIntegerField(default=0, help_text="Lower = shown first")

    class Meta:
        unique_together = ('club', 'name')
        ordering = ['club', 'display_order', 'name']

    def __str__(self):
        return f"{self.club.name} — {self.name} (${self.hourly_rate}/hr)"


class ClubMember(models.Model):
    """
    A person's membership at a club. Holds personal details, standing, and subscription info.
    A User can have memberships at multiple clubs (one ClubMember per club).
    """
    STANDING_CHOICES = [
        ('pending',     'Pending Approval'),
        ('active',      'Active'),
        ('suspended',   'Suspended'),
        ('lapsed',      'Lapsed'),
        ('resigned',    'Resigned'),
        ('transferred', 'Transferred'),
        ('non_member',  'Non-member'),   # Young Eagles, trial flights, etc.
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='club_memberships', null=True, blank=True)
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='members')

    # ── Personal details ──────────────────────────────────────────────────────
    caa_number    = models.CharField(max_length=10, blank=True)
    phone_mobile  = models.CharField(max_length=20, blank=True)
    phone_home    = models.CharField(max_length=20, blank=True)
    phone_work    = models.CharField(max_length=20, blank=True)
    address_line1 = models.CharField(max_length=255, blank=True)
    address_line2 = models.CharField(max_length=255, blank=True)
    suburb        = models.CharField(max_length=100, blank=True)
    postcode      = models.CharField(max_length=10, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    SEX_CHOICES = [('M', 'Male'), ('F', 'Female')]
    sex           = models.CharField(max_length=1, choices=SEX_CHOICES, blank=True, default='')

    # ── Next of kin (pre-fills departure declarations) ────────────────────────
    next_of_kin_name  = models.CharField(max_length=200, blank=True)
    next_of_kin_phone = models.CharField(max_length=20, blank=True)

    # ── Membership standing & subscription ───────────────────────────────────
    standing = models.CharField(max_length=20, choices=STANDING_CHOICES, default='active')
    membership_category = models.ForeignKey(MembershipCategory, on_delete=models.SET_NULL, null=True, blank=True)
    role = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True, blank=True)
    has_admin_access = models.BooleanField(
        default=False,
        help_text="Grants access to club Settings regardless of role. "
                  "Use for CFIs and club secretaries who manage the system."
    )
    instructor_grade = models.ForeignKey(
        InstructorGrade, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='members', help_text="Instructor qualification grade — determines hourly rate"
    )
    is_on_instructor_roster = models.BooleanField(
        default=False,
        help_text="Appears as an instructor row in the calendar and is selectable on bookings. "
                  "Requires the member to have an instructor-type role."
    )

    avatar               = models.ImageField(upload_to='avatars/', null=True, blank=True)
    join_date            = models.DateField(auto_now_add=True, help_text="Date first admitted")
    subscription_expires = models.DateField(null=True, blank=True, help_text="Current subscription valid until")
    last_renewed         = models.DateField(null=True, blank=True, help_text="Date of most recent subscription payment")
    resigned_at          = models.DateField(null=True, blank=True, help_text="Date membership formally ceased (ISA 2022 s.26)")

    class Meta:
        unique_together = ('user', 'club')
        ordering = ['user__last_name']

    def __str__(self):
        role_str = self.role.name if self.role else 'No role'
        return f"{self.user.get_full_name() if self.user else '—'} @ {self.club.name} ({role_str})"

    @property
    def is_current(self):
        """True when the member is active AND their subscription has not expired."""
        if self.standing != 'active':
            return False
        if self.subscription_expires and self.subscription_expires < date.today():
            return False
        return True

    @property
    def is_member(self):
        return self.standing != 'non_member'

    @property
    def is_instructor(self):
        return bool(self.role and self.role.effective_is_instructor)

    @property
    def is_admin(self):
        return self.has_admin_access or bool(self.role and self.role.effective_is_admin)

    @property
    def is_staff(self):
        return self.is_instructor or self.is_admin

    @property
    def can_access_manage(self):
        return self.is_admin or bool(self.role and (self.role.can_access_manage or self.role.is_superadmin))

    @property
    def can_access_fleet(self):
        return self.is_admin or bool(self.role and (self.role.can_access_fleet or self.role.can_access_manage or self.role.is_superadmin))

    @property
    def can_access_safety(self):
        return self.is_admin or bool(self.role and (self.role.can_access_safety or self.role.can_access_manage or self.role.is_superadmin))

    @property
    def can_access_reports(self):
        return self.is_admin or bool(self.role and (self.role.can_access_reports or self.role.is_superadmin))

    @property
    def bookings_access(self):
        if self.is_admin or (self.role and self.role.is_superadmin):
            return Role.BOOKINGS_MANAGE_ALL
        return self.role.bookings_access if self.role else Role.BOOKINGS_NONE


class MembershipHistoryEntry(models.Model):
    """Append-only audit log of membership state changes — ISA 2022 s.26 cessation date and 7-year retention."""
    EVENT_CHOICES = [
        ('joined',               'Joined'),
        ('standing_change',      'Standing changed'),
        ('category_change',      'Membership category changed'),
        ('role_change',          'Role changed'),
        ('subscription_renewed', 'Subscription renewed'),
        ('note',                 'Note'),
    ]
    club_member = models.ForeignKey(ClubMember, on_delete=models.CASCADE, related_name='membership_history')
    event_type  = models.CharField(max_length=30, choices=EVENT_CHOICES)
    changed_at  = models.DateTimeField(auto_now_add=True)
    changed_by  = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    old_value   = models.CharField(max_length=200, blank=True)
    new_value   = models.CharField(max_length=200, blank=True)
    note        = models.TextField(blank=True)

    class Meta:
        ordering = ['-changed_at']

    def __str__(self):
        return f"{self.club_member} — {self.get_event_type_display()} at {self.changed_at:%Y-%m-%d}"


# ============================================================================
# ANNOUNCEMENTS
# ============================================================================

class Announcement(models.Model):
    TYPE_CHOICES = [
        ('announcement', 'Announcement'),
        ('info',         'Information'),
        ('safety',       'Safety Notice'),
        ('event',        'Event'),
        ('flyaway',      'Fly-Away'),
    ]
    club       = models.ForeignKey('Club', on_delete=models.CASCADE, related_name='announcements')
    type       = models.CharField(max_length=20, choices=TYPE_CHOICES, default='announcement')
    title      = models.CharField(max_length=200)
    body       = models.TextField(blank=True)
    event_date = models.DateField(null=True, blank=True,
                                  help_text="Optional date — appears on calendar on that day")
    expires_at = models.DateField(null=True, blank=True,
                                  help_text="Hide from home screen after this date (blank = always show)")
    is_pinned  = models.BooleanField(default=False)
    created_by = models.ForeignKey('User', on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    TYPE_COLOR = {
        'announcement': '#2f7dd1',
        'info':         '#0891b2',
        'safety':       '#d97706',
        'event':        '#16a34a',
        'flyaway':      '#7c3aed',
    }
    TYPE_BG = {
        'announcement': '#eff6ff',
        'info':         '#ecfeff',
        'safety':       '#fffbeb',
        'event':        '#f0fdf4',
        'flyaway':      '#f5f3ff',
    }

    class Meta:
        ordering = ['-is_pinned', '-created_at']

    def __str__(self):
        return f"[{self.get_type_display()}] {self.title}"

    @property
    def color(self):
        return self.TYPE_COLOR.get(self.type, '#2f7dd1')

    @property
    def bg(self):
        return self.TYPE_BG.get(self.type, '#eff6ff')


# ============================================================================
# REGULATORY TRACKING
# ============================================================================

class CredentialType(models.TextChoices):
    """NZ CAA pilot credential categories."""
    # Licences
    PPL         = 'ppl',         'Private Pilot Licence (PPL)'
    CPL         = 'cpl',         'Commercial Pilot Licence (CPL)'
    ATPL        = 'atpl',        'Air Transport Pilot Licence (ATPL)'
    # Ratings & endorsements within a licence
    CROSS_COUNTRY = 'xc',        'Cross Country Endorsement'
    NIGHT_VFR   = 'night_vfr',   'Night VFR Endorsement'
    INSTRUMENT  = 'ir',          'Instrument Rating (IR)'
    MULTI_ENGINE = 'me',         'Multi-Engine Rating'
    TYPE_RATING = 'type',        'Type Rating'
    TAILWHEEL   = 'tailwheel',   'Tailwheel Endorsement'
    AEROBATIC   = 'aerobatic',   'Aerobatic Endorsement'
    SEAPLANE    = 'seaplane',    'Seaplane Rating'
    # Instructor certificates (NZ: C-Cat, B-Cat, A-Cat, Examiner)
    INSTRUCTOR_C = 'instr_c',    'Instructor Certificate — C-Cat'
    INSTRUCTOR_B = 'instr_b',    'Instructor Certificate — B-Cat'
    INSTRUCTOR_A = 'instr_a',    'Instructor Certificate — A-Cat'
    EXAMINER    = 'examiner',    'Flight Examiner'
    # Medical certificates
    MEDICAL_C1  = 'medical_c1',  'Medical Certificate — Class 1'
    MEDICAL_C2  = 'medical_c2',  'Medical Certificate — Class 2'
    MEDICAL_C3  = 'medical_c3',  'Medical Certificate — Class 3'
    MEDICAL_DLR9 = 'dlr9',       'DLR9 Medical'
    # Reviews (every 24 months for PPL/CPL/ATPL holders — formerly BFR)
    FLIGHT_REVIEW = 'fr',        'Flight Review (BFR)'
    OTHER       = 'other',       'Other'


class MemberCredential(models.Model):
    """
    Tracks pilot licences, ratings, endorsements, medicals, and flight reviews.
    Multiple records of the same type are allowed (e.g. two type ratings).
    """
    club_member = models.ForeignKey(ClubMember, on_delete=models.CASCADE, related_name='credentials')
    credential_type = models.CharField(max_length=20, choices=CredentialType.choices)

    # For type ratings — links to the managed aircraft type list
    aircraft_type = models.ForeignKey('AircraftType', on_delete=models.SET_NULL,
                                       null=True, blank=True, related_name='type_ratings')

    # Sub-type name — required for OTHER, optional elsewhere; for type ratings prefer aircraft_type FK
    name = models.CharField(max_length=100, blank=True,
                            help_text="Specific name — required for Other; supplementary for Type Rating (e.g. specific aircraft reg)")

    # Validity
    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)

    # Document tracking
    certificate_number = models.CharField(max_length=50, blank=True)
    notes = models.TextField(blank=True)

    # Photo/scan evidence (licence, medical cert, etc.) — FileField to allow PDFs
    evidence = models.FileField(upload_to='credentials/', null=True, blank=True)

    # Who recorded it
    created_by = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='credentials_added')

    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['credential_type', '-expiry_date']

    def __str__(self):
        label = self.get_credential_type_display()
        if self.name:
            label += f' — {self.name}'
        return f"{self.club_member.user.last_name}: {label}"

    @property
    def display_name(self):
        label = self.get_credential_type_display()
        return f"{label} — {self.name}" if self.name else label

    @property
    def days_until_expiry(self):
        if not self.expiry_date:
            return None
        return (self.expiry_date - date.today()).days

    @property
    def is_expired(self):
        return bool(self.expiry_date and self.expiry_date < date.today())

    @property
    def is_expiring_soon(self):
        d = self.days_until_expiry
        return d is not None and 0 <= d <= 60


# ============================================================================
# AIRCRAFT & MAINTENANCE
# ============================================================================

class AircraftType(models.Model):
    """Club-managed list of aircraft types. Aircraft and type-rating credentials reference this."""
    club = models.ForeignKey('Club', on_delete=models.CASCADE, related_name='aircraft_types')
    name = models.CharField(max_length=60)
    icao_designator = models.CharField(max_length=10, blank=True,
                                       help_text="ICAO type designator, e.g. C172, PA38")

    class Meta:
        ordering = ['name']
        unique_together = [('club', 'name')]

    def __str__(self):
        return self.name


class AircraftStatus(models.TextChoices):
    """Aircraft operational status. Temporary unavailability (maintenance, grounding) is
    managed via block-outs rather than status changes."""
    ONLINE = 'online', 'Active'
    RETIRED = 'retired', 'Retired'


class Aircraft(models.Model):
    """
    Aircraft in the fleet. Tracks hobbs/tacho, maintenance schedule, availability.
    """
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='aircraft')

    # Identification
    registration = models.CharField(max_length=10)
    aircraft_type = models.ForeignKey('AircraftType', on_delete=models.PROTECT,
                                       null=True, blank=True, related_name='aircraft')
    serial_number = models.CharField(max_length=50, blank=True)
    
    # Configuration
    engine_count = models.IntegerField(default=1)
    seats = models.IntegerField(default=2)
    
    # Recording instruments
    records_hobbs = models.BooleanField(default=True)
    records_tacho = models.BooleanField(default=False)
    records_airswitch = models.BooleanField(default=False)
    
    # Time calculation
    TOTAL_TIME_METHOD_CHOICES = [
        ('hobbs',      'Hobbs Meter'),
        ('tacho',      'Tachometer'),
        ('airswitch',  'Air Switch'),
    ]
    total_time_method = models.CharField(max_length=20, choices=TOTAL_TIME_METHOD_CHOICES, default='hobbs')
    
    # Fuel
    fuel_consumption_per_hour = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    # Starting meter readings when the aircraft was entered into the system
    hobbs_initial = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text="Hobbs reading when aircraft was entered into the system"
    )
    tacho_initial = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text="Tacho reading when aircraft was entered into the system"
    )
    airswitch_initial = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text="Air switch reading when aircraft was entered into the system"
    )

    # Surcharges applied per flight (e.g. 4-seat surcharge)
    surcharges = models.ManyToManyField(
        'AircraftSurchargeType', blank=True, related_name='aircraft',
        help_text="Surcharges automatically added to every flight on this aircraft"
    )

    # Maintenance time calculation (can differ from billing total_time_method)
    MAINT_SOURCE_CHOICES = [
        ('hobbs',     'Hobbs Meter'),
        ('tacho',     'Tachometer'),
        ('airswitch', 'Air Switch'),
    ]
    maint_time_source = models.CharField(
        max_length=10, choices=MAINT_SOURCE_CHOICES, default='hobbs',
        help_text="Which instrument to use when accumulating maintenance hours"
    )
    maint_time_fraction = models.DecimalField(
        max_digits=4, decimal_places=2, default='1.00',
        help_text="Fraction of raw reading counted as maintenance hours (e.g. 0.95 = 95% of tacho)"
    )
    maint_hours_initial = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text="Maintenance hours already accumulated when aircraft was entered into this system"
    )

    # Status
    status = models.CharField(max_length=20, choices=AircraftStatus.choices, default=AircraftStatus.ONLINE)
    is_available_for_hire = models.BooleanField(default=True)
    is_leased = models.BooleanField(default=False, help_text="Leased-in aircraft (not club-owned)")
    
    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ('club', 'registration')
        ordering = ['registration']
    
    def __str__(self):
        type_name = self.aircraft_type.name if self.aircraft_type_id else 'Unknown'
        return f"{self.registration} ({type_name})"
    
    @property
    def is_online(self):
        return self.status == AircraftStatus.ONLINE and self.is_available_for_hire


class MaintenanceUrgency(models.TextChoices):
    """Visual indicator for maintenance urgency."""
    GREEN = 'green', 'Within limits'
    AMBER = 'amber', 'Due soon'
    RED = 'red', 'Overdue/grounded'


class AircraftMaintenanceItem(models.Model):
    """
    Recurring maintenance tasks: 100-hour check, oil change, etc.
    Tracks both calendar and flight-hour based intervals.
    """
    aircraft = models.ForeignKey(Aircraft, on_delete=models.CASCADE, related_name='maintenance_items')
    
    # What needs doing
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    
    # Scheduling: calendar-based
    due_date = models.DateField(null=True, blank=True)
    interval_days = models.IntegerField(
        null=True, blank=True,
        help_text="Recurring calendar interval in days (e.g. 365 for annual)"
    )

    # Scheduling: maintenance-hour-based
    due_hours = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text="Cumulative maintenance hours at which this item is next due"
    )
    interval_hours = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text="Recurring interval in maintenance hours (e.g. 100 for a 100-hour check)"
    )

    # Warning thresholds — hours-based
    warn_hours  = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True,
        help_text="Amber warning when this many maintenance hours remain (default from ClubConfig)")
    alert_hours = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True,
        help_text="Red alert when this many maintenance hours remain")

    # Warning thresholds — calendar-based
    warn_days  = models.IntegerField(null=True, blank=True,
        help_text="Amber warning when this many days remain until due date")
    alert_days = models.IntegerField(null=True, blank=True,
        help_text="Red alert when this many days remain until due date")

    # When it was last done
    last_completed_date = models.DateField(null=True, blank=True)
    last_completed_hours = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)

    # Urgency (stored for fast filtering, recalculated after each check-in)
    urgency = models.CharField(max_length=10, choices=MaintenanceUrgency.choices, default=MaintenanceUrgency.GREEN)

    class Meta:
        ordering = ['urgency', 'due_date', 'due_hours']

    def __str__(self):
        return f"{self.aircraft.registration} - {self.name}"

    @property
    def days_until_due(self):
        if self.due_date:
            return (self.due_date - date.today()).days
        return None

    @property
    def current_maint_hours(self):
        """Latest cumulative maintenance hours for this aircraft."""
        entry = self.aircraft.maint_log.order_by('-date', '-id').first()
        if entry:
            return float(entry.maint_hours_total)
        return float(self.aircraft.maint_hours_initial or 0)

    @property
    def hours_remaining(self):
        if self.due_hours is None:
            return None
        return round(float(self.due_hours) - self.current_maint_hours, 2)

    def recalc_urgency(self, config=None):
        urgency = MaintenanceUrgency.GREEN
        hr = self.hours_remaining
        dr = self.days_until_due
        if hr is not None:
            alert_h = float(self.alert_hours or (config.maint_alert_hours if config else 5))
            warn_h  = float(self.warn_hours  or (config.maint_warn_hours  if config else 20))
            if hr <= 0:        urgency = MaintenanceUrgency.RED
            elif hr <= alert_h: urgency = MaintenanceUrgency.RED
            elif hr <= warn_h:  urgency = MaintenanceUrgency.AMBER
        if dr is not None:
            alert_d = self.alert_days or (config.maint_alert_days if config else 7)
            warn_d  = self.warn_days  or (config.maint_warn_days  if config else 14)
            if dr <= 0:
                urgency = MaintenanceUrgency.RED
            elif dr <= alert_d and urgency != MaintenanceUrgency.RED:
                urgency = MaintenanceUrgency.RED
            elif dr <= warn_d and urgency == MaintenanceUrgency.GREEN:
                urgency = MaintenanceUrgency.AMBER
        return urgency


class MaintenanceLogEntry(models.Model):
    """
    One entry per flight (or manual entry) recording raw meter readings and
    the resulting cumulative maintenance hours. Mirrors the paper tech log.

    Auto-created when a FlightCompletion is saved. Manual entries are allowed
    for correcting meter gaps or seeding historical data.
    """
    aircraft          = models.ForeignKey(Aircraft, on_delete=models.CASCADE, related_name='maint_log')
    flight_completion = models.OneToOneField(
        'FlightCompletion', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='maint_log_entry',
        help_text="Set when auto-created from a flight check-in"
    )
    date              = models.DateField()

    # Raw instrument end-of-flight readings (mirrors paper tech log)
    hobbs_reading     = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    tacho_reading     = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    airswitch_reading = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)

    # Computed maintenance hours
    maint_hours_flight = models.DecimalField(
        max_digits=7, decimal_places=2, default=0,
        help_text="Maintenance hours accrued this flight (source × fraction)"
    )
    maint_hours_total  = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        help_text="Cumulative maintenance hours for this aircraft after this entry"
    )

    notes      = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date', 'id']

    def __str__(self):
        return f"{self.aircraft.registration} {self.date} — {self.maint_hours_total}h total"


def create_maint_log_entry(flight_completion):
    """
    Called after a FlightCompletion is saved (check-in). Computes maintenance
    hours for this flight and appends a MaintenanceLogEntry.
    Idempotent — skips if an entry already exists for this completion.
    """
    if MaintenanceLogEntry.objects.filter(flight_completion=flight_completion).exists():
        return
    # Use the aircraft that physically departed — may differ from booking.aircraft
    # if an admin swapped the aircraft mid-flight. This ensures maintenance hours
    # are logged against the aircraft that actually flew.
    ac = flight_completion.departed_with_aircraft or flight_completion.booking.aircraft
    src = ac.maint_time_source
    frac = float(ac.maint_time_fraction or 1)

    start_map = {'hobbs': flight_completion.hobbs_start,
                 'tacho': flight_completion.tacho_start,
                 'airswitch': flight_completion.airswitch_start}
    end_map   = {'hobbs': flight_completion.hobbs_end,
                 'tacho': flight_completion.tacho_end,
                 'airswitch': flight_completion.airswitch_end}
    raw_start = start_map.get(src)
    raw_end   = end_map.get(src)

    flight_hours = 0.0
    if raw_start is not None and raw_end is not None:
        try:
            start_f = float(raw_start)
            end_f   = float(raw_end)
            if end_f >= start_f:
                flight_hours = round((end_f - start_f) * frac, 2)
        except (ValueError, TypeError):
            pass

    prev = ac.maint_log.order_by('-date', '-id').first()
    prev_total = float(prev.maint_hours_total) if prev else float(ac.maint_hours_initial or 0)

    MaintenanceLogEntry.objects.create(
        aircraft=ac,
        flight_completion=flight_completion,
        date=flight_completion.booking.scheduled_start.date(),
        hobbs_reading=flight_completion.hobbs_end,
        tacho_reading=flight_completion.tacho_end,
        airswitch_reading=flight_completion.airswitch_end,
        maint_hours_flight=flight_hours,
        maint_hours_total=round(prev_total + flight_hours, 2),
    )


# ============================================================================
# FLIGHT TYPES & BILLING
# ============================================================================

class FlightType(models.Model):
    """
    Flight categories for charging & tracking.
    """
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='flight_types')

    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20)

    # Flags
    is_billable = models.BooleanField(default=True)
    is_training = models.BooleanField(default=False)
    is_solo = models.BooleanField(default=False, help_text="Solo flights — instructor is not required")
    requires_declaration = models.BooleanField(
        default=False,
        help_text="Pilot must submit a pre-departure declaration before checking out (e.g. Private Hire)"
    )

    class Meta:
        unique_together = ('club', 'code')
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.club.name})"


class AircraftSurchargeType(models.Model):
    """
    Configurable surcharge types per club (e.g. '4-seat surcharge', 'IFR surcharge').
    Assigned to specific aircraft via M2M. Applied as a fixed amount per flight.
    """
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='surcharge_types')
    name = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ('club', 'name')
        ordering = ['name']

    def __str__(self):
        return f"{self.name} (${self.amount})"


class ChargeRate(models.Model):
    """
    Billing rates by aircraft, flight type, and time-recording method.
    """
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='charge_rates')
    aircraft = models.ForeignKey(Aircraft, on_delete=models.CASCADE, related_name='charge_rates')
    flight_type = models.ForeignKey(FlightType, on_delete=models.CASCADE, related_name='charge_rates')
    
    # Time recording basis
    TIME_METHOD_CHOICES = [
        ('hobbs',      'Hobbs Hour'),
        ('tacho',      'Tachometer Hour'),
        ('airswitch',  'Air Switch Hour'),
    ]
    time_method = models.CharField(max_length=20, choices=TIME_METHOD_CHOICES)
    
    # Rate
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    currency = models.CharField(max_length=3, default='NZD')
    
    # Modifiers
    includes_fuel = models.BooleanField(default=False)
    
    class Meta:
        unique_together = ('aircraft', 'flight_type', 'time_method')
    
    def __str__(self):
        return f"{self.aircraft.registration} - {self.flight_type.name}: ${self.amount}"


# ============================================================================
# ACCOUNTS & BILLING
# ============================================================================

class Account(models.Model):
    """
    Member's financial account. Tracks balance and credit.
    credit_limit=None means exempt from warnings (typically instructors).
    """
    PAYMENT_METHOD_CHOICES = [
        ('credit',  'Account credit (auto-deduct)'),
        ('eftpos',  'EFTPOS'),
        ('invoice', 'Invoice (bank transfer)'),
    ]

    club_member = models.OneToOneField(ClubMember, on_delete=models.CASCADE, related_name='account')

    balance      = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    credit_limit = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Max negative balance allowed. Null = exempt (e.g. instructors)."
    )
    preferred_payment_method = models.CharField(
        max_length=20, choices=PAYMENT_METHOD_CHOICES, blank=True, default='',
        help_text="Standing payment instruction. Can be overridden per flight."
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Accounts'

    def __str__(self):
        name = self.club_member.user.get_full_name() if self.club_member.user else '—'
        return f"Account: {name} ({self.balance})"

    @property
    def has_warning(self):
        """True when balance is negative beyond the allowed credit limit."""
        if self.credit_limit is None:
            return False   # exempt
        return self.balance < -self.credit_limit

    def apply_transaction(self, amount, direction):
        """Update balance in-place. Call inside an atomic block."""
        from decimal import Decimal
        amount = Decimal(str(amount))
        if direction == 'credit':
            self.balance += amount
        else:
            self.balance -= amount
        self.save(update_fields=['balance', 'updated_at'])

    def recompute_balance(self):
        """Recompute balance from transaction history. Returns computed value."""
        from django.db.models import Sum
        credits = self.transactions.filter(direction='credit').aggregate(t=Sum('amount'))['t'] or 0
        debits  = self.transactions.filter(direction='debit').aggregate(t=Sum('amount'))['t'] or 0
        return credits - debits


class AccountTransaction(models.Model):
    """
    Immutable ledger entry on a member's account.
    Every balance change must be recorded here — top-ups, flight charges,
    cancellation fees, and manual adjustments.
    Account.balance is a cached sum; recompute_balance() verifies it.
    """
    TYPE_CHOICES = [
        ('top_up',       'Account top-up'),
        ('flight',       'Flight charge'),
        ('cancellation', 'Cancellation fee'),
        ('adjustment',   'Manual adjustment'),
        ('sale',         'Sundry sale'),
    ]
    DIRECTION_CHOICES = [
        ('credit', 'Credit'),
        ('debit',  'Debit'),
    ]
    PAYMENT_METHOD_CHOICES = [
        ('bank_transfer', 'Bank transfer'),
        ('eftpos',        'EFTPOS'),
        ('cash',          'Cash'),
        ('account',       'Account credit'),
        ('other',         'Other'),
    ]

    account          = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='transactions')
    transaction_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    direction        = models.CharField(max_length=6, choices=DIRECTION_CHOICES)
    amount           = models.DecimalField(max_digits=10, decimal_places=2,
                                           help_text="Always positive; direction determines credit/debit")
    description      = models.CharField(max_length=300,
                                        help_text="Mandatory — shown on account statement")

    # Source references (at most one will be set)
    flight_completion = models.ForeignKey(
        'FlightCompletion', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='account_transactions'
    )
    booking = models.ForeignKey(
        'Booking', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='account_transactions',
        help_text="Set for cancellation fee transactions"
    )

    # Payment details (relevant for top-ups)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, blank=True)
    reference      = models.CharField(max_length=100, blank=True,
                                      help_text="Bank reference, receipt number, cheque number etc.")

    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='account_transactions')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Account transaction"

    def __str__(self):
        sign = '+' if self.direction == 'credit' else '-'
        return f"{self.account.club_member} {sign}${self.amount} — {self.description}"


class VoucherType(models.Model):
    """
    Pre-defined voucher product in the club's catalogue.
    Used to pre-fill the create-voucher form for common products.
    """
    club          = models.ForeignKey('Club', on_delete=models.CASCADE, related_name='voucher_types')
    name          = models.CharField(max_length=200)
    default_value = models.DecimalField(max_digits=8, decimal_places=2)
    description   = models.CharField(max_length=200, blank=True,
                                     help_text='Default description printed on the voucher')
    is_active     = models.BooleanField(default=True)
    sort_order    = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']
        unique_together = [('club', 'name')]

    def __str__(self):
        return f"{self.name} (${self.default_value})"


class FlyingBudget(models.Model):
    """Budgeted hours per aircraft per month for a given FY start year."""
    club    = models.ForeignKey('Club', on_delete=models.CASCADE, related_name='flying_budgets')
    aircraft = models.ForeignKey('Aircraft', on_delete=models.CASCADE, related_name='budgets')
    fy_year  = models.PositiveSmallIntegerField(help_text="Year the FY starts (e.g. 2025 for Jul 2025–Jun 2026)")
    month    = models.PositiveSmallIntegerField(help_text="Month number 1–12")
    budgeted_hours = models.DecimalField(max_digits=6, decimal_places=1, default=0)

    class Meta:
        unique_together = [('club', 'aircraft', 'fy_year', 'month')]
        ordering = ['aircraft__registration', 'fy_year', 'month']

    def __str__(self):
        return f"{self.aircraft.registration} FY{self.fy_year} m{self.month}: {self.budgeted_hours}h"


class Voucher(models.Model):
    """
    Credit voucher. Purchased externally (website, front desk); bookkeeper
    redeems it by crediting the member's account. Once redeemed it cannot
    be reused.
    """
    club        = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='vouchers')
    code        = models.CharField(max_length=30, help_text="Unique code printed on voucher")
    description = models.CharField(max_length=200, blank=True,
                                    help_text="E.g. 'Harbour Circuit Trial Flight'")
    value       = models.DecimalField(max_digits=8, decimal_places=2,
                                      help_text="Credit amount in club currency")
    is_redeemed  = models.BooleanField(default=False)
    redeemed_by  = models.ForeignKey(ClubMember, on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name='redeemed_vouchers')
    redeemed_at  = models.DateTimeField(null=True, blank=True)
    notes        = models.TextField(blank=True)
    created_by   = models.ForeignKey(User, on_delete=models.PROTECT, related_name='created_vouchers')
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('club', 'code')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.code} (${self.value}{'  redeemed' if self.is_redeemed else ''})"


# ============================================================================
# CONTACTS  (non-members: trial flights, Young Eagles, commercial clients)
# ============================================================================

class ContactType(models.Model):
    """Club-configurable contact categories (Young Eagles, Trial flight, etc.)."""
    club       = models.ForeignKey('Club', on_delete=models.CASCADE, related_name='contact_types')
    name       = models.CharField(max_length=100)
    is_active  = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']
        unique_together = [('club', 'name')]

    def __str__(self):
        return self.name


class Contact(models.Model):
    club            = models.ForeignKey('Club', on_delete=models.CASCADE, related_name='contacts')
    name            = models.CharField(max_length=200)
    email           = models.EmailField(blank=True)
    phone           = models.CharField(max_length=50, blank=True)
    is_organisation = models.BooleanField(
        default=False,
        help_text='Organisation account (school, company). Cannot be converted to a member.'
    )
    organisation    = models.CharField(
        max_length=200, blank=True,
        help_text='For individuals: sponsoring school or organisation.'
    )
    contact_type    = models.ForeignKey(
        ContactType, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='contacts'
    )
    notes           = models.TextField(blank=True)
    converted_to_member = models.ForeignKey(
        'ClubMember', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='converted_from_contact'
    )
    created_by      = models.ForeignKey(
        'User', on_delete=models.SET_NULL, null=True, blank=True, related_name='contacts_created'
    )
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def can_convert(self):
        return not self.is_organisation and self.converted_to_member_id is None


# ============================================================================
# BOOKINGS
# ============================================================================

class BookingStatus(models.TextChoices):
    """Booking lifecycle states."""
    PENDING = 'pending', 'Pending Confirmation'
    CONFIRMED = 'confirmed', 'Confirmed'
    DEPARTED = 'departed', 'Departed'
    COMPLETED = 'completed', 'Returned'
    CANCELLED = 'cancelled', 'Cancelled'


class Booking(models.Model):
    """
    Flight booking. Member requests a time slot; instructor confirms.
    Billing happens at completion.
    """
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='bookings')
    
    # Participants
    member     = models.ForeignKey(ClubMember, on_delete=models.CASCADE, related_name='bookings')
    client     = models.ForeignKey(
        'Contact', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='bookings',
        help_text='Non-member being flown (trial flight, Young Eagles, etc.)'
    )
    BILLED_CONTACT      = 'contact'
    BILLED_ORGANISATION = 'organisation'
    BILLED_CLUB         = 'club'
    BILLED_TO_CHOICES = [
        (BILLED_CONTACT,      'Client pays'),
        (BILLED_ORGANISATION, 'Organisation invoiced'),
        (BILLED_CLUB,         'Club absorbs'),
    ]
    billed_to  = models.CharField(
        max_length=20, choices=BILLED_TO_CHOICES, blank=True, default='',
        help_text='Only relevant when a non-member client is set.'
    )
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='bookings_created')
    confirmed_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='bookings_confirmed'
    )
    
    # Flight details
    aircraft = models.ForeignKey(Aircraft, on_delete=models.PROTECT, related_name='bookings')
    flight_type = models.ForeignKey(FlightType, on_delete=models.PROTECT)
    instructor = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='flights_instructed'
    )
    
    # Timing
    scheduled_start = models.DateTimeField()
    scheduled_end = models.DateTimeField()
    
    # Status & notes
    status = models.CharField(max_length=20, choices=BookingStatus.choices, default=BookingStatus.PENDING)
    description = models.TextField(blank=True)
    
    # Flags
    is_maintenance_booking = models.BooleanField(default=False)

    # Block-out interaction
    blockout_conflict = models.BooleanField(
        default=False,
        help_text="Set when a block-out was added over this existing booking. Staff must resolve."
    )
    blockout_conflict_reason = models.CharField(max_length=255, blank=True)
    blockout_override = models.BooleanField(
        default=False,
        help_text="Set when staff deliberately booked over a block-out."
    )
    
    # Departure / arrival timestamps
    departed_at = models.DateTimeField(null=True, blank=True)
    arrived_at = models.DateTimeField(null=True, blank=True)

    # Set when pilot checks out without a submitted declaration
    departed_without_declaration = models.BooleanField(default=False)
    departed_without_declaration_reason = models.CharField(max_length=500, blank=True)

    # Cancellation reason
    CANCELLATION_REASON_CHOICES = [
        ('weather',                'Weather'),
        ('aircraft_unserviceable', 'Aircraft unserviceable'),
        ('instructor_unavailable', 'Instructor unavailable'),
        ('no_longer_required',     'No longer required'),
        ('other',                  'Other'),
    ]
    cancellation_reason       = models.CharField(max_length=30, blank=True, choices=CANCELLATION_REASON_CHOICES)
    cancellation_reason_other = models.CharField(max_length=200, blank=True)

    # Slot release — set when staff/member explicitly frees the slot for others
    slot_released = models.BooleanField(default=False)
    slot_released_at = models.DateTimeField(null=True, blank=True)
    slot_released_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='slots_released',
    )

    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-scheduled_start']
        indexes = [
            models.Index(fields=['club', 'scheduled_start']),
            models.Index(fields=['member', 'status']),
        ]
    
    def __str__(self):
        return f"{self.member.user.last_name} - {self.aircraft.registration} ({self.scheduled_start.date()})"

    @property
    def display_status(self):
        """Human label that distinguishes returned-unpaid from completed-paid."""
        if self.status == 'completed':
            try:
                if self.flight_completion.is_paid:
                    return 'Completed'
            except Exception:
                pass
            return 'Returned'
        return self.get_status_display()

    @property
    def display_status_key(self):
        """CSS class key for display_status."""
        if self.status == 'completed':
            try:
                if self.flight_completion.is_paid:
                    return 'completed'
            except Exception:
                pass
            return 'returned'
        return self.status

    def save(self, *args, **kwargs):
        if self.confirmed_by:
            confirmer = ClubMember.objects.filter(user=self.confirmed_by, club=self.club).first()
            if not (confirmer and confirmer.is_staff):
                raise ValueError("Only staff can confirm bookings.")
        super().save(*args, **kwargs)


class BookingAuditLog(models.Model):
    """
    Immutable event log. Records every state change on a booking.
    Used for compliance & audit trail.
    """
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='audit_logs')
    
    # Event
    EVENT_CHOICES = [
        ('created', 'Created'),
        ('confirmed', 'Confirmed'),
        ('field_changed', 'Field Changed'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
        ('warning_acknowledged', 'Warning Acknowledged'),
    ]
    event_type = models.CharField(max_length=30, choices=EVENT_CHOICES)
    
    # Who did it
    user = models.ForeignKey(User, on_delete=models.PROTECT)
    
    # What changed (optional)
    field_name = models.CharField(max_length=100, blank=True)
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    
    # Context (optional)
    notes = models.TextField(blank=True)
    
    # Timestamp (immutable)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['created_at']
        verbose_name_plural = 'Booking Audit Logs'
    
    def __str__(self):
        return f"{self.booking} - {self.get_event_type_display()} by {self.user.last_name}"


class FlightCompletion(models.Model):
    """
    Logged flight data: actual hours, charges, payment.
    Created when a booking is checked in (arrived). Triggers billing.
    Charges are built as FlightChargeItem line items; total_charge is their sum.
    """
    OUTCOME_CHOICES = [
        ('completed',         'Flight completed normally'),
        ('aborted_ground',    'Aborted on ground (pre-takeoff)'),
        ('early_return_tech', 'Early return — technical'),
        ('early_return_wx',   'Early return — weather'),
        ('diverted',          'Diverted / landed away'),
    ]
    PAYMENT_METHOD_CHOICES = [
        ('credit',  'Account credit'),
        ('eftpos',  'EFTPOS'),
        ('invoice', 'Invoice (bank transfer)'),
    ]

    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='flight_completion')

    # Flight outcome
    outcome = models.CharField(max_length=30, choices=OUTCOME_CHOICES, default='completed')
    outcome_notes = models.TextField(
        blank=True, help_text="Required when outcome is not 'completed normally'"
    )
    # Snapshot original flight type if changed at check-in
    original_flight_type = models.ForeignKey(
        FlightType, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='changed_completions',
        help_text="Populated if flight type was changed from booking type at check-in"
    )

    # Departure snapshots — capture which aircraft and instructor actually departed.
    # Booking.aircraft / instructor can be changed mid-flight by admin; these fields
    # preserve the original so maintenance hours are logged against the right aircraft.
    departed_with_aircraft = models.ForeignKey(
        'Aircraft', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='departed_completions',
        help_text="Aircraft that physically departed — may differ from booking.aircraft if swapped mid-flight"
    )
    departed_with_instructor = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='departed_completions',
        help_text="Instructor at departure — may differ from booking.instructor if swapped mid-flight"
    )

    # Actual flight time readings
    hobbs_start      = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    hobbs_end        = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    tacho_start      = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    tacho_end        = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    airswitch_start  = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    airswitch_end    = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)

    # Calculated hours (based on aircraft total_time_method)
    actual_flight_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)

    # Fuel surcharge rate snapshotted at departure time (rate per hour at that moment)
    fuel_surcharge_rate_snapshot = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text="Fuel surcharge rate ($/hr) locked at time of departure"
    )
    # Instructor hourly rate snapshotted at check-in time
    instructor_rate_snapshot = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text="Instructor hourly rate locked at time of check-in"
    )

    # Total charge (sum of FlightChargeItem rows — computed, stored for fast display)
    total_charge = models.DecimalField(max_digits=8, decimal_places=2, default=0)

    # Payment cache — denormalised from FlightPayment rows via _sync_payment_cache()
    payment_method = models.CharField(
        max_length=20, choices=PAYMENT_METHOD_CHOICES + [('split', 'Split'), ('cash', 'Cash')],
        blank=True, default=''
    )
    amount_paid = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    paid_at = models.DateTimeField(null=True, blank=True)

    # Meter gap — recorded when the start reading doesn't follow on from the previous end
    meter_gap_note = models.TextField(
        blank=True, default='',
        help_text="Explanation required when start reading doesn't match the previous flight's end reading"
    )

    # Audit
    logged_by = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.booking} — {self.actual_flight_hours}h ({self.get_outcome_display()})"

    @property
    def is_paid(self):
        """Fully settled (zero-charge flights are always settled)."""
        if not self.total_charge:
            return True
        return self.paid_at is not None and self.amount_paid >= self.total_charge

    @property
    def is_partially_paid(self):
        return self.amount_paid > 0 and self.amount_paid < self.total_charge

    @property
    def balance_owing(self):
        return max(0, self.total_charge - self.amount_paid)

    def _sync_payment_cache(self):
        """Recompute denormalised payment fields from FlightPayment rows."""
        from decimal import Decimal as _D
        from django.db.models import Sum as _Sum
        paid_qs = self.payments.filter(paid_at__isnull=False)
        paid_total = paid_qs.aggregate(t=_Sum('amount'))['t'] or _D('0')
        methods = list(paid_qs.values_list('method', flat=True).distinct())
        last_paid = paid_qs.order_by('paid_at').last()
        self.amount_paid = paid_total
        self.paid_at = last_paid.paid_at if (last_paid and paid_total >= self.total_charge) else None
        self.payment_method = methods[0] if len(methods) == 1 else ('split' if methods else '')
        self.save(update_fields=['amount_paid', 'paid_at', 'payment_method'])


class FlightSegment(models.Model):
    """
    One pilot's portion of a split flight.  Meter readings are contiguous —
    segment N end = segment N+1 start.  All configured instruments are recorded
    even if only one drives payment calculation.
    """
    flight_completion = models.ForeignKey(
        FlightCompletion, on_delete=models.CASCADE, related_name='segments'
    )
    member   = models.ForeignKey('ClubMember', on_delete=models.PROTECT, related_name='flight_segments')
    sequence = models.PositiveSmallIntegerField()

    hobbs_start     = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    hobbs_end       = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    tacho_start     = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    tacho_end       = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    airswitch_start = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    airswitch_end   = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)

    hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)

    class Meta:
        ordering = ['sequence']
        unique_together = [('flight_completion', 'sequence')]

    def __str__(self):
        return f"Seg {self.sequence} — {self.member} ({self.hours}h)"


class FlightPayment(models.Model):
    """
    One payee row for a flight. A single flight may have multiple rows (split payment/flight).
    paid_at=None means allocated but not yet collected. paid_at set means money received.
    """
    PAYMENT_METHOD_CHOICES = [
        ('credit',  'Account credit'),
        ('eftpos',  'EFTPOS'),
        ('cash',    'Cash'),
        ('invoice', 'Invoice (bank transfer)'),
    ]
    completion  = models.ForeignKey(FlightCompletion, on_delete=models.CASCADE, related_name='payments')
    member      = models.ForeignKey('ClubMember', on_delete=models.PROTECT, related_name='flight_payments')
    amount      = models.DecimalField(max_digits=8, decimal_places=2)
    method      = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES)
    paid_at     = models.DateTimeField(null=True, blank=True)
    recorded_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='+')
    created_at  = models.DateTimeField(auto_now_add=True)

    @property
    def is_paid(self):
        return self.paid_at is not None

    def get_method_display(self):
        return dict(self.PAYMENT_METHOD_CHOICES).get(self.method, self.method)

    class Meta:
        ordering = ['created_at']


# ============================================================================
# AERODROMES & FUEL SURCHARGE RATES
# ============================================================================

class Aerodrome(models.Model):
    """
    Aerodromes the club has landing fee arrangements with (e.g. NZMS, NZPP).
    Fee types (full stop, touch & go, night surcharge, etc.) are configured
    per-aerodrome via AerodromeFeeType.
    """
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='aerodromes')
    icao_code = models.CharField(max_length=4, help_text="4-letter ICAO code, e.g. NZMS")
    name = models.CharField(max_length=200, help_text="e.g. Masterton")
    is_active = models.BooleanField(default=True)
    is_home = models.BooleanField(default=False, help_text="Home aerodrome for this club")
    notes = models.TextField(
        blank=True,
        help_text="Agreement terms, billing cycle, contact info, anything instructors need to know"
    )

    class Meta:
        unique_together = ('club', 'icao_code')
        ordering = ['icao_code']

    def __str__(self):
        return f"{self.icao_code} — {self.name}"


class WeatherWebcam(models.Model):
    club         = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='webcams')
    name         = models.CharField(max_length=100)
    url          = models.URLField(max_length=500, help_text="Webcam page link (used for 'Open' button)")
    embed_code   = models.TextField(blank=True, help_text="Optional iframe embed code — overrides image embed")
    description  = models.CharField(max_length=200, blank=True)
    display_order= models.PositiveIntegerField(default=0)
    is_active    = models.BooleanField(default=True)

    class Meta:
        ordering = ['display_order', 'name']

    def __str__(self):
        return f"{self.name} ({self.club})"


class AerodromeFeeType(models.Model):
    """
    A named fee type at an aerodrome (e.g. Full stop, Touch & go, Night surcharge).
    Instructors can define these freely per aerodrome. The default_amount is used
    to pre-fill the charge at flight completion but can be overridden.
    """
    aerodrome = models.ForeignKey(Aerodrome, on_delete=models.CASCADE, related_name='fee_types')
    name = models.CharField(max_length=100, help_text="e.g. Full stop, Touch & go, Night surcharge")
    default_amount = models.DecimalField(max_digits=8, decimal_places=2, default=0)

    class Meta:
        unique_together = ('aerodrome', 'name')
        ordering = ['name']

    def __str__(self):
        return f"{self.aerodrome.icao_code} — {self.name} (${self.default_amount})"


class FuelSurchargeRate(models.Model):
    """
    Dated per-hour fuel rate. Used for dry-hire aircraft where fuel is charged separately,
    or as a club-wide fuel levy on top of wet rates.
    aircraft=None means the rate applies to all aircraft without a specific override.
    """
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='fuel_surcharge_rates')
    aircraft = models.ForeignKey(
        'Aircraft', on_delete=models.CASCADE, null=True, blank=True,
        related_name='fuel_rates',
        help_text="Leave blank for a club-wide rate; set for a per-aircraft override."
    )
    rate = models.DecimalField(max_digits=8, decimal_places=2, help_text="Per Hobbs/Tacho hour")
    effective_from = models.DateField()
    notes = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True, help_text="Uncheck to pause fuel charging without deleting the rate")

    class Meta:
        ordering = ['-effective_from']
        verbose_name = "Fuel rate"

    def __str__(self):
        target = self.aircraft.registration if self.aircraft else "all aircraft"
        return f"{self.club.name} fuel rate ${self.rate}/hr ({target}) from {self.effective_from}"

    @classmethod
    def current_rate(cls, club, aircraft, on_date=None):
        """
        Return the effective fuel rate for a given aircraft on a given date.
        Aircraft-specific rate takes priority over the club-wide default.
        Returns None if no rate is configured (wet hire with no fuel component).
        """
        d = on_date or date.today()
        if aircraft:
            specific = cls.objects.filter(
                club=club, aircraft=aircraft, effective_from__lte=d, is_active=True
            ).order_by('-effective_from').first()
            if specific:
                return specific
        return cls.objects.filter(
            club=club, aircraft__isnull=True, effective_from__lte=d, is_active=True
        ).order_by('-effective_from').first()


# ============================================================================
# SLOT-RELEASE NOTIFICATIONS
# ============================================================================

class NotificationPreference(models.Model):
    """
    A member's opt-in for slot-released emails.
    Empty M2M sets mean "any" — i.e. notify for all aircraft / all instructors.
    All filters must pass simultaneously.
    """
    club_member = models.OneToOneField(
        ClubMember, on_delete=models.CASCADE, related_name='notification_prefs'
    )

    # Resource filters (empty = any)
    aircraft = models.ManyToManyField(
        'Aircraft', blank=True, related_name='notification_prefs',
        help_text="Notify only when one of these aircraft is freed. Empty = any aircraft."
    )
    instructors = models.ManyToManyField(
        'User', blank=True, related_name='notification_prefs',
        help_text="Notify only when one of these instructors is freed. Empty = any instructor."
    )

    # Time-horizon filter
    max_days_ahead = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Only notify for slots starting within this many days. Null = no limit."
    )

    # Per-type alert toggles (default ON, except slot_released which is opt-in OFF)
    booking_confirmed           = models.BooleanField(default=True)
    booking_cancelled           = models.BooleanField(default=True)
    booking_reminder            = models.BooleanField(default=True)
    credential_expiring         = models.BooleanField(default=True)
    subscription_expiring       = models.BooleanField(default=True)
    instructor_booking_urgent   = models.BooleanField(default=True)
    instructor_booking_upcoming = models.BooleanField(default=True)
    maintenance_alert           = models.BooleanField(default=True)
    lapsed_credentials          = models.BooleanField(default=True)
    slot_released               = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Notification preference"

    def __str__(self):
        name = self.club_member.user.get_full_name() if self.club_member.user else '—'
        return f"{name} — slot notifications"

    def matches(self, booking):
        """True if a released booking falls within this member's preferences."""
        if self.aircraft.exists() and not self.aircraft.filter(id=booking.aircraft_id).exists():
            return False
        if self.instructors.exists() and not self.instructors.filter(id=booking.instructor_id).exists():
            return False
        if self.max_days_ahead is not None:
            days_away = (booking.scheduled_start.date() - date.today()).days
            if not (0 <= days_away <= self.max_days_ahead):
                return False
        return True


class Notification(models.Model):
    """In-app notification inbox item for a club member."""
    TYPES = [
        ('booking_confirmed',          'Booking confirmed'),
        ('booking_cancelled',          'Booking cancelled'),
        ('booking_reminder',           'Booking reminder'),
        ('credential_expiring',        'Credential expiring'),
        ('subscription_expiring',      'Subscription expiring'),
        ('instructor_booking_urgent',  'New booking assigned (urgent)'),
        ('instructor_booking_upcoming','New booking assigned'),
        ('maintenance_alert',          'Maintenance alert'),
        ('lapsed_credentials',         'Lapsed credentials — flight today'),
    ]
    club_member       = models.ForeignKey(ClubMember, on_delete=models.CASCADE, related_name='notifications')
    notification_type = models.CharField(max_length=40, choices=TYPES)
    subject           = models.CharField(max_length=200)
    body              = models.TextField(blank=True)
    action_url        = models.CharField(max_length=500, blank=True)
    is_read           = models.BooleanField(default=False)
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.club_member} — {self.subject}"


class SlotReleaseNotification(models.Model):
    """
    Log of every slot-release email dispatched.
    unique_together prevents sending the same member duplicate emails if
    the release action is somehow triggered twice.
    """
    booking = models.ForeignKey(
        Booking, on_delete=models.CASCADE, related_name='release_notifications'
    )
    club_member = models.ForeignKey(
        ClubMember, on_delete=models.CASCADE, related_name='slot_notifications_received'
    )
    email = models.EmailField(help_text="Snapshot of address at send time.")
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('booking', 'club_member')
        ordering = ['-sent_at']

    def __str__(self):
        return f"Release notice → {self.club_member} for {self.booking}"


class SlotWatch(models.Model):
    """
    A member explicitly watching a specific booking in case it becomes available.
    Created via "Watch this slot" on the calendar. Notified on release ahead of
    (or alongside) the general NotificationPreference broadcast.
    notified_at is stamped when the release email is sent, preventing double-sends.
    """
    booking = models.ForeignKey(
        Booking, on_delete=models.CASCADE, related_name='watchers'
    )
    club_member = models.ForeignKey(
        ClubMember, on_delete=models.CASCADE, related_name='watched_slots'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    notified_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Stamped when the slot-release email was sent to this watcher."
    )

    class Meta:
        unique_together = ('booking', 'club_member')
        ordering = ['created_at']

    def __str__(self):
        name = self.club_member.user.get_full_name() if self.club_member.user else '—'
        return f"{name} watching {self.booking}"


def members_to_notify_for_release(booking):
    """
    Return ClubMembers to email when a slot is released.
    Watchers first (preserves the spirit of expressed interest), then opted-in
    members whose NotificationPreference matches. No duplicates, no double-sends.
    Excludes the booking's own member and anyone already notified.
    """
    already_notified = set(
        SlotReleaseNotification.objects.filter(booking=booking)
        .values_list('club_member_id', flat=True)
    )

    # Watchers who haven't been notified yet
    watchers = list(
        SlotWatch.objects
        .filter(booking=booking, notified_at__isnull=True)
        .exclude(club_member=booking.member)
        .exclude(club_member_id__in=already_notified)
        .select_related('club_member__user')
        .values_list('club_member', flat=True)
    )
    watcher_set = set(watchers)

    # General preference matches, excluding watchers (they're already included)
    prefs = (
        NotificationPreference.objects
        .filter(club_member__club=booking.club, club_member__standing='active')
        .exclude(club_member=booking.member)
        .exclude(club_member_id__in=already_notified)
        .exclude(club_member_id__in=watcher_set)
        .prefetch_related('aircraft', 'instructors', 'club_member__user')
    )
    pref_matches = [p.club_member for p in prefs if p.matches(booking)]

    # Resolve watcher IDs back to ClubMember objects
    watcher_members = list(
        ClubMember.objects.filter(id__in=watchers).select_related('user')
    )

    return watcher_members + pref_matches


class BlockOutType(models.Model):
    """Extensible block-out categories per club (Maintenance, Lunch, Admin, etc.)."""
    TARGET_AIRCRAFT = 'aircraft'
    TARGET_INSTRUCTOR = 'instructor'
    TARGET_ALL = 'all'
    TARGET_CHOICES = [
        ('aircraft',   'Aircraft'),
        ('instructor', 'Instructor'),
        ('all',        'All resources'),
    ]

    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='blockout_types')
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=7, default='#9aa3ad', help_text="Hex colour for the calendar")

    target = models.CharField(
        max_length=20, choices=TARGET_CHOICES, default='all',
        help_text="Which resource type this block-out applies to."
    )
    is_hard = models.BooleanField(
        default=True,
        help_text="Hard: blocks bookings (staff can override). Soft: advisory — anyone can confirm and proceed. "
                  "Aircraft block-out types are always hard regardless of this flag."
    )

    class Meta:
        ordering = ['target', 'name']

    def __str__(self):
        return self.name

    @property
    def effective_is_hard(self):
        """Aircraft blocks are always hard; instructor blocks respect is_hard."""
        return self.target == self.TARGET_AIRCRAFT or self.is_hard


class BlockOut(models.Model):
    """
    A period during which a resource (or all resources) is unavailable for booking.
    Set by instructors/admins. Subtracted from availability search.
    """
    RECUR_CHOICES = [
        ('one_off', 'One-off'),
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
    ]
    SCOPE_CHOICES = [
        ('all', 'All resources'),
        ('aircraft', 'Specific aircraft'),
        ('instructors', 'Specific instructors'),
    ]

    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='blockouts')
    blockout_type = models.ForeignKey(BlockOutType, on_delete=models.SET_NULL, null=True, blank=True)
    label = models.CharField(max_length=150, blank=True, help_text="Optional note, e.g. '100hr check'")

    scope = models.CharField(max_length=20, choices=SCOPE_CHOICES, default='all')
    aircraft = models.ManyToManyField('Aircraft', blank=True, related_name='blockouts')
    instructors = models.ManyToManyField('User', blank=True, related_name='blockouts')

    recurrence = models.CharField(max_length=10, choices=RECUR_CHOICES, default='one_off')
    # one_off: uses date. weekly: uses weekday (0=Mon..6=Sun). daily: every day.
    date = models.DateField(null=True, blank=True, help_text="For one-off blocks")
    weekday = models.IntegerField(null=True, blank=True, help_text="0=Mon..6=Sun, for weekly")

    all_day = models.BooleanField(default=False)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)

    # Optional bounding window for recurring blocks
    active_from = models.DateField(null=True, blank=True)
    active_until = models.DateField(null=True, blank=True)

    created_by = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True, related_name='created_blockouts')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['recurrence', 'start_time']

    def __str__(self):
        t = self.blockout_type.name if self.blockout_type else 'Block'
        return f"{t} ({self.get_recurrence_display()})"

    def applies_on(self, day):
        """Does this block-out occur on the given date?"""
        if self.active_from and day < self.active_from:
            return False
        if self.active_until and day > self.active_until:
            return False
        if self.recurrence == 'one_off':
            return self.date == day
        if self.recurrence == 'daily':
            return True
        if self.recurrence == 'weekly':
            return self.weekday == day.weekday()
        return False

    def affects_aircraft(self, aircraft):
        if self.scope == 'all':
            return True
        if self.scope == 'aircraft':
            return self.aircraft.filter(id=aircraft.id).exists()
        return False  # instructor-scoped blocks don't block aircraft

    def affects_instructor(self, user):
        if self.scope == 'all':
            return True
        if self.scope == 'instructors':
            return self.instructors.filter(id=user.id).exists()
        return False

    def affects_booking(self, booking):
        """Does this block-out cover the resource(s) this booking uses?"""
        if self.scope == 'all':
            return True
        if self.scope == 'aircraft':
            return booking.aircraft_id and self.aircraft.filter(id=booking.aircraft_id).exists()
        if self.scope == 'instructors':
            return booking.instructor_id and self.instructors.filter(id=booking.instructor_id).exists()
        return False

    def interval_on(self, day):
        """Return (start_dt, end_dt) this block occupies on a given day, or None."""
        from django.utils import timezone as _tz
        if not self.applies_on(day):
            return None
        if self.all_day or not (self.start_time and self.end_time):
            s = datetime.combine(day, time(0, 0))
            e = datetime.combine(day, time(23, 59))
        else:
            s = datetime.combine(day, self.start_time)
            e = datetime.combine(day, self.end_time)
        if _tz.is_naive(s):
            s = _tz.make_aware(s)
        if _tz.is_naive(e):
            e = _tz.make_aware(e)
        return (s, e)

    def overlaps_booking(self, booking):
        """True if this block-out, on the booking's local day(s), overlaps it in time and scope."""
        from django.utils import timezone as _tz
        if not self.affects_booking(booking):
            return False
        local_start = _tz.localtime(booking.scheduled_start)
        local_end = _tz.localtime(booking.scheduled_end)
        for day in {local_start.date(), local_end.date()}:
            iv = self.interval_on(day)
            if iv and iv[0] < booking.scheduled_end and iv[1] > booking.scheduled_start:
                return True
        return False

    def rescan_bookings(self):
        """
        Re-evaluate conflict flags for bookings potentially affected by this block-out.
        Scans from midnight today so bookings earlier today are not missed when a
        block-out is added after those bookings have already ended.
        """
        from django.utils import timezone as _tz
        from datetime import datetime, time as _time
        today_start = _tz.make_aware(datetime.combine(_tz.localdate(), _time.min))
        affected = Booking.objects.filter(
            club=self.club,
            scheduled_end__gte=today_start,
        ).exclude(status='cancelled')
        for b in affected:
            recompute_blockout_conflict(b)


def blockout_conflicts_for_booking(booking):
    """
    Return the list of BlockOut objects that conflict with this booking
    (overlap in time AND cover its aircraft/instructor by scope).
    """
    from django.utils import timezone as _tz
    # Use the booking's LOCAL day(s); a UTC-stored booking can span the local
    # date boundary, so check both the local start and end dates.
    local_start = _tz.localtime(booking.scheduled_start)
    local_end = _tz.localtime(booking.scheduled_end)
    days = {local_start.date(), local_end.date()}
    hits = []
    for bo in BlockOut.objects.filter(club=booking.club).prefetch_related('aircraft', 'instructors', 'blockout_type'):
        if not bo.affects_booking(booking):
            continue
        for day in days:
            iv = bo.interval_on(day)
            if iv and iv[0] < booking.scheduled_end and iv[1] > booking.scheduled_start:
                hits.append(bo)
                break
    return hits


def recompute_blockout_conflict(booking, save=True):
    """
    Recompute the blockout_conflict flag for a booking from current block-outs.
    A staff override (blockout_override=True) suppresses the conflict flag.
    Returns True if the booking is currently in conflict (pre-override).
    """
    hits = blockout_conflicts_for_booking(booking)
    in_conflict = bool(hits)

    if booking.blockout_override:
        # staff already chose to book over a block; keep clear but remember reason
        new_flag = False
        reason = ''
    else:
        new_flag = in_conflict
        reason = ''
        if hits:
            first = hits[0]
            tname = first.blockout_type.name if first.blockout_type else (first.label or 'block-out')
            reason = f"Overlaps {tname}"

    if save and (booking.blockout_conflict != new_flag or booking.blockout_conflict_reason != reason):
        booking.blockout_conflict = new_flag
        booking.blockout_conflict_reason = reason
        booking.save(update_fields=['blockout_conflict', 'blockout_conflict_reason'])
    else:
        booking.blockout_conflict = new_flag
        booking.blockout_conflict_reason = reason
    return in_conflict


class InstructorAvailability(models.Model):
    """
    Declares when an instructor is on-roster and available to be booked.
    The availability search intersects aircraft free time with this schedule.
    An instructor with no records here is assumed available during all operating hours.
    """
    RECURRENCE_CHOICES = [
        ('weekly', 'Weekly (recurring)'),
        ('one_off', 'Specific date'),
    ]
    WEEKDAY_CHOICES = [
        (0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'),
        (3, 'Thursday'), (4, 'Friday'), (5, 'Saturday'), (6, 'Sunday'),
    ]

    club_member = models.ForeignKey(
        ClubMember, on_delete=models.CASCADE, related_name='availability_windows'
    )
    recurrence = models.CharField(max_length=10, choices=RECURRENCE_CHOICES, default='weekly')

    # For weekly recurrence
    weekday = models.IntegerField(null=True, blank=True, choices=WEEKDAY_CHOICES,
                                  help_text='0=Mon … 6=Sun')
    # For one-off
    date = models.DateField(null=True, blank=True)

    all_day = models.BooleanField(default=True, help_text='Available the full operating day')
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)

    # Optional bounding window for recurring entries
    active_from = models.DateField(null=True, blank=True)
    active_until = models.DateField(null=True, blank=True)

    notes = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['recurrence', 'weekday', 'start_time']

    def __str__(self):
        name = self.club_member.user.get_full_name() if self.club_member.user else '?'
        if self.recurrence == 'weekly':
            day = dict(self.WEEKDAY_CHOICES).get(self.weekday, '?')
            hours = 'all day' if self.all_day else f"{self.start_time}–{self.end_time}"
            return f"{name} — {day} {hours}"
        return f"{name} — {self.date}"

    def applies_on(self, day):
        if self.active_from and day < self.active_from:
            return False
        if self.active_until and day > self.active_until:
            return False
        if self.recurrence == 'one_off':
            return self.date == day
        if self.recurrence == 'weekly':
            return self.weekday == day.weekday()
        return False

    def interval_on(self, day, day_start, day_end):
        """Return (start_dt, end_dt) this availability window covers on a given day."""
        from django.utils import timezone as _tz
        if not self.applies_on(day):
            return None
        if self.all_day or not (self.start_time and self.end_time):
            return (day_start, day_end)
        s = _tz.make_aware(datetime.combine(day, self.start_time))
        e = _tz.make_aware(datetime.combine(day, self.end_time))
        return (max(s, day_start), min(e, day_end))


# --- Block-out signals: keep booking conflict flags in sync -------------
from django.db.models.signals import post_save, post_delete, m2m_changed
from django.dispatch import receiver


@receiver(post_save, sender=BlockOut)
def _blockout_saved(sender, instance, **kwargs):
    # Defer until M2M is set if scope needs it; rescan is cheap & idempotent.
    instance.rescan_bookings()


@receiver(post_delete, sender=BlockOut)
def _blockout_deleted(sender, instance, **kwargs):
    # On delete, re-evaluate all upcoming bookings so stale flags clear.
    from django.utils import timezone as _tz
    for b in Booking.objects.filter(club=instance.club, scheduled_end__gte=_tz.now()).exclude(status='cancelled'):
        recompute_blockout_conflict(b)


@receiver(m2m_changed, sender=BlockOut.aircraft.through)
def _blockout_aircraft_changed(sender, instance, action, **kwargs):
    if action in ('post_add', 'post_remove', 'post_clear'):
        instance.rescan_bookings()


@receiver(m2m_changed, sender=BlockOut.instructors.through)
def _blockout_instructors_changed(sender, instance, action, **kwargs):
    if action in ('post_add', 'post_remove', 'post_clear'):
        instance.rescan_bookings()


# ============================================================================
# DEPARTURE DECLARATIONS
# ============================================================================

PASSENGER_CONSENT_TEXT = (
    "By saving this person's contact details, you confirm they have given their consent "
    "for the club to store their name, phone number, and next-of-kin details for flight "
    "safety and search and rescue purposes. This information is held securely, used only "
    "for these purposes, and may be disclosed to search and rescue authorities if required. "
    "It will not be shared with any other party. They may request removal of their details "
    "at any time by contacting the club."
)


class FrequentPassenger(models.Model):
    """
    Saved passenger details on a member's profile for re-use in departure declarations.
    Storing requires explicit passenger consent (see PASSENGER_CONSENT_TEXT).
    """
    club_member = models.ForeignKey(
        ClubMember, on_delete=models.CASCADE, related_name='frequent_passengers'
    )
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20)
    next_of_kin_name = models.CharField(max_length=200)
    next_of_kin_phone = models.CharField(max_length=20)

    consent_given = models.BooleanField(default=False)
    consent_given_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} (pax for {self.club_member})"


class DepartureDeclaration(models.Model):
    """
    Pre-departure self-declaration for flights requiring one (e.g. Private Hire).
    Pilot completes this before checking out. Can be drafted any time after booking
    is confirmed; submission locks it. Hard block on completion if missing and
    departed_without_declaration is not acknowledged by admin.
    """
    booking = models.OneToOneField(
        Booking, on_delete=models.CASCADE, related_name='declaration'
    )
    authorising_instructor = models.ForeignKey(
        ClubMember, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='authorised_declarations',
        help_text="Instructor who verbally authorised this flight. Recorded only — no active approval required."
    )
    submitted_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='submitted_declarations'
    )

    # Route & intentions
    route_intentions = models.TextField(help_text="Planned route and intentions")
    destination = models.CharField(max_length=100, blank=True, help_text="Primary destination aerodrome")
    estimated_return = models.DateTimeField(
        null=True, blank=True,
        help_text="Estimated return time — used to flag overdue aircraft"
    )
    sar_time = models.DateTimeField(
        null=True, blank=True,
        help_text="SARTIME — the date/time at which SAR should be notified if the aircraft has not returned"
    )
    is_cross_country = models.BooleanField(default=False)

    # Next of kin (copied from member profile, overridable per flight)
    next_of_kin_name = models.CharField(max_length=200)
    next_of_kin_phone = models.CharField(max_length=20)
    next_of_kin_from_profile = models.BooleanField(
        default=True, help_text="Was next of kin auto-filled from member profile?"
    )

    # Pilot confirmations
    confirm_aip = models.BooleanField(default=False, verbose_name="AIP reviewed")
    confirm_weather = models.BooleanField(default=False, verbose_name="Weather checked")
    confirm_fuel = models.BooleanField(default=False, verbose_name="Fuel requirements confirmed")
    confirm_pickets = models.BooleanField(
        default=False, verbose_name="Pickets carried",
        help_text="Only required for cross-country flights"
    )
    confirm_maps = models.BooleanField(default=False, verbose_name="Maps carried")
    confirm_fuel_card = models.BooleanField(default=False, verbose_name="Fuel card carried")
    confirm_afm = models.BooleanField(default=False, verbose_name="AFM in aircraft")
    confirm_flight_plan = models.BooleanField(default=False, verbose_name="Flight plan filed")

    # Draft / submission state
    is_draft = models.BooleanField(default=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    staleness_acknowledged = models.BooleanField(
        default=False,
        help_text="Pilot confirmed weather/NOTAMs re-checked when declaration is >6h old"
    )

    # Amendment tracking (re-open before check-out, then re-submit)
    amended_at = models.DateTimeField(null=True, blank=True)
    amendment_reason = models.CharField(max_length=500, blank=True)

    # Audit
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Departure declaration"

    def __str__(self):
        status = "draft" if self.is_draft else "submitted"
        return f"Declaration for {self.booking} ({status})"

    @property
    def is_stale(self):
        """True if submitted more than 6 hours ago — weather/NOTAMs may be outdated."""
        if not self.submitted_at:
            return False
        from django.utils import timezone as _tz
        return (_tz.now() - self.submitted_at).total_seconds() > 6 * 3600

    @property
    def is_complete(self):
        """All required confirmations ticked and not a draft."""
        if self.is_draft:
            return False
        required = [
            self.confirm_aip, self.confirm_weather, self.confirm_fuel,
            self.confirm_maps, self.confirm_fuel_card, self.confirm_afm,
        ]
        if self.is_cross_country:
            required.append(self.confirm_pickets)
        return all(required) and bool(self.next_of_kin_name) and bool(self.next_of_kin_phone)


class DeclarationPassenger(models.Model):
    """
    Passenger listed on a departure declaration.
    Always stores a snapshot of details at declaration time — historical records
    remain intact even if the FrequentPassenger entry is later deleted.
    """
    declaration = models.ForeignKey(
        DepartureDeclaration, on_delete=models.CASCADE, related_name='passengers'
    )
    frequent_passenger = models.ForeignKey(
        FrequentPassenger, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='declaration_appearances',
        help_text="Source frequent-passenger record (for reference only — data is snapshotted)"
    )

    # Always-snapshotted fields
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20)
    next_of_kin_name = models.CharField(max_length=200)
    next_of_kin_phone = models.CharField(max_length=20)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} on {self.declaration}"


# ============================================================================
# FLIGHT CHARGE ITEMS
# ============================================================================

class FlightChargeItem(models.Model):
    """
    Individual line item in a flight's charge breakdown.
    The sum of all items for a FlightCompletion = FlightCompletion.total_charge.
    One-off/sundry items require a description.
    """
    ITEM_TYPE_CHOICES = [
        ('hire',       'Aircraft hire'),
        ('fuel',       'Fuel'),
        ('instructor', 'Instructor fee'),
        ('surcharge',  'Aircraft surcharge'),
        ('landing',    'Landing fee'),
        ('one_off',    'Sundry / one-off'),
    ]

    flight_completion = models.ForeignKey(
        FlightCompletion, on_delete=models.CASCADE, related_name='charge_items'
    )
    segment = models.ForeignKey(
        'FlightSegment', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='charge_items'
    )
    item_type = models.CharField(max_length=20, choices=ITEM_TYPE_CHOICES)
    description = models.CharField(
        max_length=300,
        help_text="Mandatory for sundry items; auto-populated for standard items"
    )
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['item_type', 'created_at']

    def __str__(self):
        return f"{self.get_item_type_display()} — ${self.amount}"


class FlightLandingEntry(models.Model):
    """
    One fee line for one aerodrome on a flight. One record per fee type used
    (e.g. separate rows for full stops and touch & goes at the same aerodrome).
    Supports both configured aerodromes and custom/unknown ones entered on the fly.
    """
    flight_completion = models.ForeignKey(
        FlightCompletion, on_delete=models.CASCADE, related_name='landing_entries'
    )
    # Configured aerodrome (null if custom/unknown)
    aerodrome = models.ForeignKey(
        Aerodrome, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='landing_entries'
    )
    # Custom aerodrome fields (used when aerodrome is null)
    custom_icao = models.CharField(max_length=4, blank=True)
    custom_name = models.CharField(max_length=200, blank=True)
    save_as_aerodrome = models.BooleanField(
        default=False,
        help_text="Offer to save this custom aerodrome to the configured list"
    )
    # Fee type (null if custom/typed by instructor)
    fee_type = models.ForeignKey(
        AerodromeFeeType, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='landing_entries'
    )
    # Snapshot / custom fee type name
    fee_type_name = models.CharField(
        max_length=100, default='',
        help_text="Pre-filled from fee type; editable for custom entries"
    )
    quantity = models.PositiveIntegerField(default=1)
    unit_amount = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        help_text="Rate at time of logging (snapshot of fee_type.default_amount or manually entered)"
    )
    total_fee = models.DecimalField(max_digits=8, decimal_places=2, default=0)

    class Meta:
        ordering = ['aerodrome__icao_code', 'custom_icao', 'fee_type_name']

    def __str__(self):
        code = self.aerodrome.icao_code if self.aerodrome else self.custom_icao
        return f"{code} — {self.fee_type_name} × {self.quantity} = ${self.total_fee}"

    def save(self, *args, **kwargs):
        self.total_fee = self.unit_amount * self.quantity
        super().save(*args, **kwargs)


# ============================================================================
# INVOICING
# ============================================================================

class Invoice(models.Model):
    STATUS_DRAFT = 'draft'
    STATUS_SENT  = 'sent'
    STATUS_PAID  = 'paid'
    STATUS_VOID  = 'void'
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('sent',  'Sent / not paid'),
        ('paid',  'Paid'),
        ('void',  'Void'),
    ]

    club              = models.ForeignKey('Club', on_delete=models.CASCADE, related_name='invoices')
    member            = models.ForeignKey('ClubMember', on_delete=models.SET_NULL,
                                           null=True, related_name='invoices')
    flight_completion = models.OneToOneField('FlightCompletion', on_delete=models.SET_NULL,
                                              null=True, blank=True, related_name='invoice')

    invoice_number = models.PositiveIntegerField()
    issue_date     = models.DateField()
    due_date       = models.DateField()

    description = models.CharField(max_length=200, blank=True)
    notes       = models.TextField(blank=True)

    status   = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')
    sent_at  = models.DateTimeField(null=True, blank=True)
    paid_at  = models.DateTimeField(null=True, blank=True)

    # Snapshot of GST rate at time of invoice creation
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=15)

    # Payments reconciled against this invoice by the accountant
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Set on subscription invoices — copied to member.subscription_expires when invoice is marked paid
    subscription_expiry_date = models.DateField(null=True, blank=True)

    created_by = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='invoices_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-invoice_number']
        unique_together = [('club', 'invoice_number')]

    def __str__(self):
        prefix = self.club.config.invoice_number_prefix if hasattr(self.club, 'config') else ''
        return f"Invoice {prefix}{self.invoice_number}"

    @property
    def display_number(self):
        try:
            prefix = self.club.config.invoice_number_prefix
        except Exception:
            prefix = ''
        return f"{prefix}{self.invoice_number}"

    @property
    def total(self):
        from decimal import Decimal
        return sum((item.amount for item in self.line_items.all()), Decimal('0'))

    @property
    def balance_due(self):
        from decimal import Decimal
        return max(Decimal('0'), self.total - self.amount_paid)

    @property
    def gst_amount(self):
        """Extract included GST from total (e.g. 15% inclusive → total × 3/23)."""
        from decimal import Decimal
        rate = Decimal(str(self.gst_rate)) / 100
        return round(self.total * rate / (1 + rate), 2)

    @property
    def is_overdue(self):
        from datetime import date
        return (self.status == self.STATUS_SENT and
                self.due_date is not None and
                self.due_date < date.today())

    @property
    def days_overdue(self):
        if not self.is_overdue:
            return 0
        from datetime import date
        return (date.today() - self.due_date).days

    @property
    def age_bucket(self):
        d = self.days_overdue
        if d <= 0:   return 'current'
        if d <= 30:  return '1-30'
        if d <= 60:  return '31-60'
        if d <= 90:  return '61-90'
        return '90+'


class InvoiceLineItem(models.Model):
    invoice     = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='line_items')
    description = models.CharField(max_length=200)
    quantity    = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    unit        = models.CharField(max_length=20, blank=True)
    rate        = models.DecimalField(max_digits=10, decimal_places=2)
    amount      = models.DecimalField(max_digits=10, decimal_places=2)
    sort_order  = models.IntegerField(default=0)

    # Optional back-link to the source charge item
    charge_item = models.ForeignKey('FlightChargeItem', on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name='invoice_lines')

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f"{self.description} — ${self.amount}"


# ============================================================================
# OCCURRENCE / SAFETY REPORTING
# ============================================================================

class OccurrenceType(models.Model):
    """Configurable occurrence/incident categories per club."""
    club        = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='occurrence_types')
    name        = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    is_active   = models.BooleanField(default=True)
    sort_order  = models.IntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']
        unique_together = ('club', 'name')

    def __str__(self):
        return self.name


class OccurrenceReport(models.Model):
    STATUS_DRAFT     = 'draft'
    STATUS_SUBMITTED = 'submitted'
    STATUS_REVIEWED  = 'reviewed'
    STATUS_CLOSED    = 'closed'
    STATUS_CHOICES = [
        (STATUS_DRAFT,     'Draft'),
        (STATUS_SUBMITTED, 'Submitted'),
        (STATUS_REVIEWED,  'Reviewed'),
        (STATUS_CLOSED,    'Closed'),
    ]

    club              = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='occurrence_reports')
    occurrence_type   = models.ForeignKey(OccurrenceType, on_delete=models.PROTECT, related_name='reports')
    reported_by       = models.ForeignKey(ClubMember, on_delete=models.PROTECT, related_name='occurrence_reports')
    status            = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_SUBMITTED)

    # When and where
    date_of_occurrence  = models.DateField()
    time_of_occurrence  = models.TimeField(null=True, blank=True)
    location            = models.CharField(max_length=200, blank=True, help_text='Aerodrome ICAO, airspace, or description')

    # Optional flight link
    aircraft            = models.ForeignKey('Aircraft', on_delete=models.SET_NULL, null=True, blank=True, related_name='occurrence_reports')
    related_booking     = models.ForeignKey('Booking', on_delete=models.SET_NULL, null=True, blank=True, related_name='occurrence_reports')

    # Report content
    description         = models.TextField(help_text='Describe what happened')
    immediate_action    = models.TextField(blank=True, help_text='Any immediate action taken')

    # Admin review
    reviewed_by         = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_occurrences')
    reviewed_at         = models.DateTimeField(null=True, blank=True)
    review_notes        = models.TextField(blank=True)

    is_safety_risk      = models.BooleanField(default=False, help_text='Reporter or reviewer flagged as potential safety risk')

    reported_at         = models.DateTimeField(auto_now_add=True)
    updated_at          = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-reported_at']

    def __str__(self):
        return f"{self.occurrence_type} — {self.date_of_occurrence} ({self.reported_by.user.get_full_name()})"

    @property
    def is_open(self):
        return self.status in (self.STATUS_SUBMITTED, self.STATUS_DRAFT)

    @property
    def all_actions_resolved(self):
        return not self.actions.filter(status='open').exists()


class OccurrenceAction(models.Model):
    STATUS_OPEN       = 'open'
    STATUS_COMPLETE   = 'complete'
    STATUS_OVERRIDDEN = 'overridden'
    STATUS_CHOICES = [
        (STATUS_OPEN,       'Open'),
        (STATUS_COMPLETE,   'Complete'),
        (STATUS_OVERRIDDEN, 'Overridden'),
    ]
    report        = models.ForeignKey(OccurrenceReport, on_delete=models.CASCADE, related_name='actions')
    description   = models.TextField()
    assigned_to   = models.ForeignKey('ClubMember', on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_actions')
    due_date      = models.DateField(null=True, blank=True)
    status        = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN)
    completed_by  = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='completed_actions')
    completed_at  = models.DateTimeField(null=True, blank=True)
    override_note = models.TextField(blank=True)
    created_by    = models.ForeignKey('ClubMember', on_delete=models.SET_NULL, null=True, blank=True, related_name='created_actions')
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return self.description[:60]


class OccurrenceAuditEntry(models.Model):
    report    = models.ForeignKey(OccurrenceReport, on_delete=models.CASCADE, related_name='audit_entries')
    actor     = models.ForeignKey('ClubMember', on_delete=models.SET_NULL, null=True, blank=True, related_name='occurrence_audit_entries')
    verb      = models.CharField(max_length=80)
    note      = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']


# ============================================================================
# WEB PUSH SUBSCRIPTIONS
# ============================================================================

class PushSubscription(models.Model):
    """
    Browser-level Web Push subscription for a club member.
    One member can have multiple subscriptions (different devices/browsers).
    """
    club_member = models.ForeignKey(ClubMember, on_delete=models.CASCADE, related_name='push_subscriptions')
    endpoint    = models.TextField(unique=True)
    p256dh      = models.TextField()     # browser public key
    auth        = models.TextField()     # auth secret
    created_at  = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Push sub for {self.club_member} ({self.endpoint[:60]}…)"

    class Meta:
        ordering = ['-created_at']
