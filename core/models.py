from django.db import models
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
    theme_banner = models.CharField(max_length=7, default='#1b3a5c', help_text="Top banner background")
    theme_primary = models.CharField(max_length=7, default='#3b82f6', help_text="Buttons, links, active controls")
    theme_accent = models.CharField(max_length=7, default='#f59e0b', help_text="Accent / highlights")
    theme_confirmed = models.CharField(max_length=7, default='#16a34a', help_text="Confirmed booking pills")
    theme_pending = models.CharField(max_length=7, default='#f97316', help_text="Pending booking pills")
    theme_departed = models.CharField(max_length=7, default='#d97706', help_text="Departed booking pills")
    theme_returned = models.CharField(max_length=7, default='#2563eb', help_text="Returned (awaiting payment) booking pills")
    theme_completed_paid = models.CharField(max_length=7, default='#7c3aed', help_text="Completed & paid booking pills")
    theme_weekend = models.CharField(max_length=7, default='#f0f9ff', help_text="Weekend shading in search")
    theme_atypical = models.CharField(max_length=7, default='#f1f5f9', help_text="Outside-typical-hours shading")

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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.club.name} Config"


class Role(models.Model):
    """Extensible system roles per club."""
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='roles')
    name = models.CharField(max_length=50)  # 'instructor', 'admin', 'member'
    
    class Meta:
        unique_together = ('club', 'name')
    
    def __str__(self):
        return f"{self.club.name} - {self.name}"


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

    avatar               = models.ImageField(upload_to='avatars/', null=True, blank=True)
    join_date            = models.DateField(auto_now_add=True, help_text="Date first admitted")
    subscription_expires = models.DateField(null=True, blank=True, help_text="Current subscription valid until")
    last_renewed         = models.DateField(null=True, blank=True, help_text="Date of most recent subscription payment")

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
        return self.role and self.role.name.lower() == 'instructor'

    @property
    def is_admin(self):
        return self.has_admin_access or (self.role and self.role.name.lower() == 'admin')

    @property
    def is_staff(self):
        return self.is_instructor or self.is_admin or (self.role and self.role.name.lower() == 'staff')


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

    # Photo evidence (scanned licence, medical cert, etc.)
    evidence = models.ImageField(upload_to='credentials/', null=True, blank=True)

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
    ONLINE = 'online', 'Online'
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
        ('tacho_less_5', 'Tacho - 5%'),
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
    
    # Scheduling: flight-hour based
    due_hours = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    
    # When it was last done
    last_completed_date = models.DateField(null=True, blank=True)
    last_completed_hours = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    
    # Urgency (calculated, for UI display)
    urgency = models.CharField(max_length=10, choices=MaintenanceUrgency.choices, default=MaintenanceUrgency.GREEN)
    
    class Meta:
        ordering = ['due_date', 'due_hours']
    
    def __str__(self):
        return f"{self.aircraft.registration} - {self.name}"
    
    @property
    def days_until_due(self):
        if self.due_date:
            delta = self.due_date - date.today()
            return delta.days
        return None


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
    member = models.ForeignKey(ClubMember, on_delete=models.CASCADE, related_name='bookings')
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
        on_delete=models.SET_NULL, 
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
                if self.flight_completion.paid_at:
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
                if self.flight_completion.paid_at:
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

    # Payment — amount_paid accumulates across partial payments
    payment_method = models.CharField(
        max_length=20, choices=PAYMENT_METHOD_CHOICES, blank=True, default=''
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
        """Fully settled."""
        return self.paid_at is not None and self.amount_paid >= self.total_charge

    @property
    def is_partially_paid(self):
        return self.amount_paid > 0 and self.amount_paid < self.total_charge

    @property
    def balance_owing(self):
        return max(0, self.total_charge - self.amount_paid)


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
    notes = models.TextField(
        blank=True,
        help_text="Agreement terms, billing cycle, contact info, anything instructors need to know"
    )

    class Meta:
        unique_together = ('club', 'icao_code')
        ordering = ['icao_code']

    def __str__(self):
        return f"{self.icao_code} — {self.name}"


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
        ('sent',  'Sent — awaiting payment'),
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
