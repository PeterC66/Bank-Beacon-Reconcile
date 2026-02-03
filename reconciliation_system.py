"""
Bank Beacon Reconciliation System
Core matching logic for reconciling bank transactions with accounting entries.

Features:
- 1-to-1 and 1-to-2 matching
- Common amount weighting (£13, £9.50, £6.50)
- Date proximity matching (within 7 days)
- Name similarity matching (surname + initial)
- Beacon exclusivity (each Beacon matches only one Bank transaction)
- Auto-confirmation of high-confidence matches
- Optimized for large datasets with progress reporting
"""

import csv
import json
import os
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Tuple, Dict, Callable
from difflib import SequenceMatcher
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum


class MatchStatus(Enum):
    """Status of a match decision."""
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SKIPPED = "skipped"


@dataclass
class BankTransaction:
    """Represents a bank transaction."""
    id: str
    date: datetime
    type: str
    description: str
    amount: Decimal
    raw_data: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'date': self.date.strftime('%d-%b-%y'),
            'type': self.type,
            'description': self.description,
            'amount': str(self.amount)
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'BankTransaction':
        return cls(
            id=data['id'],
            date=datetime.strptime(data['date'], '%d-%b-%y'),
            type=data['type'],
            description=data['description'],
            amount=Decimal(data['amount']),
            raw_data=data.get('raw_data', {})
        )


@dataclass
class BeaconEntry:
    """Represents a Beacon accounting entry."""
    id: str
    date: datetime
    trans_no: str
    payee: str
    amount: Decimal
    detail: str
    raw_data: Dict = field(default_factory=dict)
    matched: bool = False

    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'date': self.date.strftime('%d/%m/%Y'),
            'trans_no': self.trans_no,
            'payee': self.payee,
            'amount': str(self.amount),
            'detail': self.detail,
            'matched': self.matched
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'BeaconEntry':
        return cls(
            id=data['id'],
            date=datetime.strptime(data['date'], '%d/%m/%Y'),
            trans_no=data['trans_no'],
            payee=data['payee'],
            amount=Decimal(data['amount']),
            detail=data['detail'],
            raw_data=data.get('raw_data', {}),
            matched=data.get('matched', False)
        )


@dataclass
class MatchSuggestion:
    """Represents a suggested match between bank and beacon transactions."""
    id: str
    bank_transaction: BankTransaction
    beacon_entries: List[BeaconEntry]  # 1 or 2 entries
    confidence_score: float
    match_type: str  # "1-to-1" or "1-to-2"
    status: MatchStatus = MatchStatus.PENDING
    amount_score: float = 0.0
    date_score: float = 0.0
    name_score: float = 0.0

    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'bank_transaction': self.bank_transaction.to_dict(),
            'beacon_entries': [e.to_dict() for e in self.beacon_entries],
            'confidence_score': self.confidence_score,
            'match_type': self.match_type,
            'status': self.status.value,
            'amount_score': self.amount_score,
            'date_score': self.date_score,
            'name_score': self.name_score
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'MatchSuggestion':
        return cls(
            id=data['id'],
            bank_transaction=BankTransaction.from_dict(data['bank_transaction']),
            beacon_entries=[BeaconEntry.from_dict(e) for e in data['beacon_entries']],
            confidence_score=data['confidence_score'],
            match_type=data['match_type'],
            status=MatchStatus(data['status']),
            amount_score=data.get('amount_score', 0.0),
            date_score=data.get('date_score', 0.0),
            name_score=data.get('name_score', 0.0)
        )


class ReconciliationSystem:
    """Main reconciliation system for matching bank and beacon transactions."""

    # Common amounts that are weak matching signals
    COMMON_AMOUNTS = [Decimal('13.00'), Decimal('9.50'), Decimal('6.50')]

    # Auto-confirm thresholds
    AUTO_CONFIRM_COMMON_THRESHOLD = 0.90  # >90% for common amounts
    AUTO_CONFIRM_OTHER_THRESHOLD = 0.80   # >80% for other amounts

    # Date tolerance in days
    DATE_TOLERANCE_DAYS = 7

    def __init__(self,
                 bank_file: str = "Bank_Transactions.csv",
                 beacon_file: str = "Beacon_Entries.csv",
                 state_file: str = "reconciliation_state.json",
                 member_lookup_file: str = "member_lookup.csv",
                 base_dir: str = None):
        # Use script directory as base if not specified
        if base_dir is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        self.base_dir = base_dir

        # Resolve file paths relative to base directory
        self.bank_file = os.path.join(base_dir, bank_file) if not os.path.isabs(bank_file) else bank_file
        self.beacon_file = os.path.join(base_dir, beacon_file) if not os.path.isabs(beacon_file) else beacon_file
        self.state_file = os.path.join(base_dir, state_file) if not os.path.isabs(state_file) else state_file
        self.member_lookup_file = os.path.join(base_dir, member_lookup_file) if not os.path.isabs(member_lookup_file) else member_lookup_file

        self.bank_transactions: List[BankTransaction] = []
        self.beacon_entries: List[BeaconEntry] = []
        self.match_suggestions: List[MatchSuggestion] = []
        self.confirmed_matches: List[MatchSuggestion] = []
        self.rejected_matches: List[MatchSuggestion] = []

        # Member lookup dictionary: mem_no -> {status, forename, surname}
        self.member_lookup: Dict[str, Dict] = {}

        # Track which beacon entries are already matched
        self.matched_beacon_ids: set = set()
        # Track which bank transactions have been rejected (to restore status on reload)
        self.rejected_bank_ids: set = set()

        # Index for fast lookups (built during generate_suggestions)
        self._beacon_by_amount: Dict[Decimal, List[BeaconEntry]] = {}
        self._beacon_amounts: set = set()

        # Progress callback
        self.progress_callback: Optional[Callable[[int, int, str], None]] = None

    def load_data(self):
        """Load transactions from CSV files."""
        self.bank_transactions = self._load_bank_transactions()
        self.beacon_entries = self._load_beacon_entries()
        self._load_member_lookup()
        self._load_state()

    def _load_bank_transactions(self) -> List[BankTransaction]:
        """Load bank transactions from CSV."""
        transactions = []

        if not os.path.exists(self.bank_file):
            return transactions

        with open(self.bank_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader):
                try:
                    # Parse date in format DD-MMM-YY
                    date = datetime.strptime(row['Date'].strip(), '%d-%b-%y')
                    amount = Decimal(row['Amount'].strip().replace(',', ''))

                    transaction = BankTransaction(
                        id=f"BANK_{idx:04d}",
                        date=date,
                        type=row['Type'].strip(),
                        description=row['Description'].strip(),
                        amount=amount,
                        raw_data=dict(row)
                    )
                    transactions.append(transaction)
                except (ValueError, KeyError) as e:
                    print(f"Warning: Could not parse bank row {idx}: {e}")

        return transactions

    def _load_beacon_entries(self) -> List[BeaconEntry]:
        """Load beacon entries from CSV."""
        entries = []

        if not os.path.exists(self.beacon_file):
            return entries

        with open(self.beacon_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader):
                try:
                    # Parse date in format DD/MM/YYYY
                    date = datetime.strptime(row['date'].strip(), '%d/%m/%Y')
                    amount = Decimal(row['amount'].strip().replace(',', ''))

                    entry = BeaconEntry(
                        id=f"BEACON_{idx:04d}",
                        date=date,
                        trans_no=row['trans_no'].strip(),
                        payee=row['payee'].strip(),
                        amount=amount,
                        detail=row.get('detail', '').strip(),
                        raw_data=dict(row)
                    )
                    entries.append(entry)
                except (ValueError, KeyError) as e:
                    print(f"Warning: Could not parse beacon row {idx}: {e}")

        return entries

    def _load_member_lookup(self):
        """Load member lookup from CSV file."""
        self.member_lookup = {}

        if not os.path.exists(self.member_lookup_file):
            print(f"Warning: Member lookup file not found: {self.member_lookup_file}")
            return

        with open(self.member_lookup_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    mem_no = row['mem_no'].strip()
                    self.member_lookup[mem_no] = {
                        'status': row['status'].strip(),
                        'forename': row['forename'].strip(),
                        'surname': row['surname'].strip()
                    }
                except KeyError as e:
                    print(f"Warning: Could not parse member lookup row: {e}")

        print(f"Loaded {len(self.member_lookup)} member lookup entries")

    def extract_member_numbers(self, description: str) -> List[str]:
        """Extract all member numbers from a bank description.

        Extracts:
        - Standalone numbers (e.g., "823", "1783")
        - Numbers from U3A references (e.g., "U3A1679" -> "1679")
        - Numbers in patterns like "1786/1785"
        """
        import re

        numbers = []

        # Extract numbers from U3A references (e.g., U3A1679)
        u3a_matches = re.findall(r'u3a(\d+)', description, flags=re.IGNORECASE)
        numbers.extend(u3a_matches)

        # Extract standalone numbers and slash-separated numbers
        # First, remove U3A references to avoid double-counting
        clean_desc = re.sub(r'u3a\d+', '', description, flags=re.IGNORECASE)

        # Find all numbers (including slash-separated like 1786/1785)
        number_matches = re.findall(r'\b(\d+)\b', clean_desc)
        numbers.extend(number_matches)

        # Remove duplicates while preserving order
        seen = set()
        unique_numbers = []
        for num in numbers:
            if num not in seen:
                seen.add(num)
                unique_numbers.append(num)

        return unique_numbers

    def lookup_member(self, mem_no: str) -> Optional[Dict]:
        """Look up a member by their member number.

        Returns dict with 'status', 'forename', 'surname' if found, else None.
        """
        return self.member_lookup.get(mem_no)

    def get_member_lookup_text(self, description: str) -> str:
        """Get member lookup text for display in GUI.

        Returns formatted text showing member info for all numbers in description.
        """
        numbers = self.extract_member_numbers(description)

        if not numbers:
            return "No mem_no given"

        lines = []
        for num in numbers:
            member = self.lookup_member(num)
            if member:
                name = f"{member['forename']} {member['surname']}"
                if member['status'].lower() != 'current':
                    name += f" ({member['status']})"
                lines.append(f"Member {num}: {name}")
            else:
                lines.append(f"{num} is an unknown mem_no")

        return "\n".join(lines)

    def _load_state(self):
        """Load saved state from JSON file."""
        if not os.path.exists(self.state_file):
            return

        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)

            # Load matched beacon IDs
            self.matched_beacon_ids = set(state.get('matched_beacon_ids', []))

            # Load confirmed matches
            self.confirmed_matches = [
                MatchSuggestion.from_dict(m)
                for m in state.get('confirmed_matches', [])
            ]

            # Load rejected bank IDs
            self.rejected_bank_ids = set(state.get('rejected_bank_ids', []))

            # Load rejected matches
            self.rejected_matches = [
                MatchSuggestion.from_dict(m)
                for m in state.get('rejected_matches', [])
            ]

            # Mark beacon entries as matched
            for entry in self.beacon_entries:
                if entry.id in self.matched_beacon_ids:
                    entry.matched = True

        except (json.JSONDecodeError, KeyError) as e:
            print(f"Warning: Could not load state: {e}")

    def save_state(self):
        """Save current state to JSON file."""
        state = {
            'matched_beacon_ids': list(self.matched_beacon_ids),
            'confirmed_matches': [m.to_dict() for m in self.confirmed_matches],
            'rejected_bank_ids': list(self.rejected_bank_ids),
            'rejected_matches': [m.to_dict() for m in self.rejected_matches]
        }

        with open(self.state_file, 'w') as f:
            json.dump(state, f, indent=2)

    def _build_beacon_index(self, available_beacon: List[BeaconEntry]):
        """Build index of beacon entries by amount for fast lookup."""
        self._beacon_by_amount = defaultdict(list)
        self._beacon_amounts = set()

        for beacon in available_beacon:
            self._beacon_by_amount[beacon.amount].append(beacon)
            self._beacon_amounts.add(beacon.amount)

    def _report_progress(self, current: int, total: int, message: str):
        """Report progress if callback is set."""
        if self.progress_callback:
            self.progress_callback(current, total, message)

    def generate_suggestions(self, progress_callback: Callable[[int, int, str], None] = None,
                             include_confirmed: bool = False) -> List[MatchSuggestion]:
        """Generate match suggestions for bank transactions.

        Args:
            progress_callback: Optional callback for progress updates
            include_confirmed: If True, include bank transactions that already have confirmed matches
        """
        self.progress_callback = progress_callback
        self.match_suggestions = []
        suggestion_id = 0

        # Get bank transactions to process
        confirmed_bank_ids = {m.bank_transaction.id for m in self.confirmed_matches}

        if include_confirmed:
            # Include all bank transactions
            bank_to_process = list(self.bank_transactions)
        else:
            # Only unmatched bank transactions
            bank_to_process = [t for t in self.bank_transactions
                              if t.id not in confirmed_bank_ids]

        # Get available beacon entries (not matched)
        available_beacon = [e for e in self.beacon_entries
                          if e.id not in self.matched_beacon_ids]

        total_bank = len(bank_to_process)

        if total_bank == 0:
            # If include_confirmed, still add the confirmed matches
            if include_confirmed:
                for match in self.confirmed_matches:
                    self.match_suggestions.append(match)
            return self._sort_suggestions()

        # Build index for fast lookups
        self._report_progress(0, total_bank, "Building index...")
        self._build_beacon_index(available_beacon)

        # Process each bank transaction
        for idx, bank_txn in enumerate(bank_to_process):
            self._report_progress(idx + 1, total_bank, f"Processing {bank_txn.description[:20]}...")

            # Check if this bank transaction already has a confirmed match
            if bank_txn.id in confirmed_bank_ids:
                # Find and add the existing confirmed match
                for match in self.confirmed_matches:
                    if match.bank_transaction.id == bank_txn.id:
                        if match not in self.match_suggestions:
                            self.match_suggestions.append(match)
                        break
                continue

            # Find 1-to-1 matches (optimized)
            one_to_one = self._find_one_to_one_matches_fast(bank_txn)

            # Find 1-to-2 matches (optimized)
            one_to_two = self._find_one_to_two_matches_fast(bank_txn)

            # Combine and sort by confidence
            all_matches = one_to_one + one_to_two
            all_matches.sort(key=lambda m: m.confidence_score, reverse=True)

            # Take the best match if any exist
            if all_matches:
                best_match = all_matches[0]
                best_match.id = f"MATCH_{suggestion_id:04d}"
                self.match_suggestions.append(best_match)
                suggestion_id += 1
            else:
                # Create a suggestion with no beacon matches for unmatched bank txn
                suggestion = MatchSuggestion(
                    id=f"MATCH_{suggestion_id:04d}",
                    bank_transaction=bank_txn,
                    beacon_entries=[],
                    confidence_score=0.0,
                    match_type="no-match"
                )
                self.match_suggestions.append(suggestion)
                suggestion_id += 1

        # Auto-confirm high-confidence matches
        self._report_progress(total_bank, total_bank, "Auto-confirming high-confidence matches...")
        self._auto_confirm_matches()

        # Restore rejected status for previously rejected bank transactions
        self._restore_rejected_status()

        return self._sort_suggestions()

    def _sort_suggestions(self) -> List[MatchSuggestion]:
        """Sort suggestions by bank transaction, then status, then confidence."""
        # Status priority: confirmed=0, pending=1, skipped=2, rejected=3
        status_priority = {
            MatchStatus.CONFIRMED: 0,
            MatchStatus.PENDING: 1,
            MatchStatus.SKIPPED: 2,
            MatchStatus.REJECTED: 3,
        }

        self.match_suggestions.sort(key=lambda m: (
            m.bank_transaction.id,  # Group by bank transaction
            status_priority.get(m.status, 1),  # Then by status priority
            -m.confidence_score  # Then by confidence (highest first, hence negative)
        ))

        return self.match_suggestions

    def _restore_rejected_status(self):
        """Restore REJECTED status for previously rejected bank transactions."""
        for match in self.match_suggestions:
            if match.bank_transaction.id in self.rejected_bank_ids:
                if match.status == MatchStatus.PENDING:
                    match.status = MatchStatus.REJECTED
                    if match not in self.rejected_matches:
                        self.rejected_matches.append(match)

    def _find_one_to_one_matches_fast(self, bank_txn: BankTransaction) -> List[MatchSuggestion]:
        """Find 1-to-1 matches using indexed lookup."""
        matches = []

        # Only look at beacon entries with matching amount
        if bank_txn.amount not in self._beacon_by_amount:
            return matches

        for beacon in self._beacon_by_amount[bank_txn.amount]:
            # Calculate date score first (fast rejection)
            date_score = self._calculate_date_score(bank_txn.date, beacon.date)
            if date_score == 0:
                continue  # Outside date tolerance

            name_score = self._calculate_name_score(bank_txn.description, beacon.payee)
            amount_score = self._calculate_amount_score(bank_txn.amount)

            # Skip if name is 0% and amount is common - not a real match
            if name_score == 0 and bank_txn.amount in self.COMMON_AMOUNTS:
                continue

            # Calculate overall confidence
            confidence = self._calculate_confidence(
                amount_score, date_score, name_score, bank_txn.amount
            )

            match = MatchSuggestion(
                id="",  # Will be assigned later
                bank_transaction=bank_txn,
                beacon_entries=[beacon],
                confidence_score=confidence,
                match_type="1-to-1",
                amount_score=amount_score,
                date_score=date_score,
                name_score=name_score
            )
            matches.append(match)

        return matches

    def _find_one_to_two_matches_fast(self, bank_txn: BankTransaction) -> List[MatchSuggestion]:
        """Find 1-to-2 matches using indexed lookup (optimized)."""
        matches = []
        bank_amount = bank_txn.amount
        bank_date = bank_txn.date

        # For each unique amount in beacon entries
        checked_pairs = set()

        for amount1 in self._beacon_amounts:
            amount2 = bank_amount - amount1

            # Skip if amount2 doesn't exist or if we've already checked this pair
            if amount2 not in self._beacon_amounts:
                continue

            pair_key = tuple(sorted([amount1, amount2]))
            if pair_key in checked_pairs:
                continue
            checked_pairs.add(pair_key)

            # Get beacons with these amounts
            beacons1 = self._beacon_by_amount[amount1]
            beacons2 = self._beacon_by_amount[amount2]

            # If same amount, need to handle differently
            if amount1 == amount2:
                # Pairs from same list
                for i, b1 in enumerate(beacons1):
                    # Check date early for b1
                    date_score1 = self._calculate_date_score(bank_date, b1.date)
                    if date_score1 == 0:
                        continue

                    for b2 in beacons1[i+1:]:
                        # Check date early for b2
                        date_score2 = self._calculate_date_score(bank_date, b2.date)
                        if date_score2 == 0:
                            continue

                        match = self._create_two_match(bank_txn, b1, b2, date_score1, date_score2)
                        if match:
                            matches.append(match)
            else:
                # Pairs from different lists
                for b1 in beacons1:
                    date_score1 = self._calculate_date_score(bank_date, b1.date)
                    if date_score1 == 0:
                        continue

                    for b2 in beacons2:
                        date_score2 = self._calculate_date_score(bank_date, b2.date)
                        if date_score2 == 0:
                            continue

                        match = self._create_two_match(bank_txn, b1, b2, date_score1, date_score2)
                        if match:
                            matches.append(match)

        return matches

    def _create_two_match(self, bank_txn: BankTransaction,
                          beacon1: BeaconEntry, beacon2: BeaconEntry,
                          date_score1: float, date_score2: float) -> Optional[MatchSuggestion]:
        """Create a 1-to-2 match suggestion."""
        date_score = (date_score1 + date_score2) / 2

        # Calculate name scores
        name_score1 = self._calculate_name_score(bank_txn.description, beacon1.payee)
        name_score2 = self._calculate_name_score(bank_txn.description, beacon2.payee)
        name_score = (name_score1 + name_score2) / 2

        # Check if individual amounts are common
        is_common1 = beacon1.amount in self.COMMON_AMOUNTS
        is_common2 = beacon2.amount in self.COMMON_AMOUNTS

        # Skip if name is 0% and amounts are common - not a real match
        if name_score == 0 and (is_common1 and is_common2):
            return None

        # Calculate amount score based on whether amounts are common
        if is_common1 and is_common2:
            amount_score = 0.3  # Both common, weak signal
        elif is_common1 or is_common2:
            amount_score = 0.6  # One common
        else:
            amount_score = 1.0  # Neither common

        # Calculate overall confidence
        # 1-to-2 matches get a slight penalty since they're more complex
        base_confidence = self._calculate_confidence(
            amount_score, date_score, name_score, bank_txn.amount
        )
        confidence = base_confidence * 0.9  # 10% penalty for complexity

        return MatchSuggestion(
            id="",
            bank_transaction=bank_txn,
            beacon_entries=[beacon1, beacon2],
            confidence_score=confidence,
            match_type="1-to-2",
            amount_score=amount_score,
            date_score=date_score,
            name_score=name_score
        )

    def _auto_confirm_matches(self):
        """Auto-confirm matches that exceed confidence thresholds."""
        auto_confirmed = 0

        for match in self.match_suggestions:
            if match.status != MatchStatus.PENDING:
                continue
            if not match.beacon_entries:
                continue

            # Check if amount is common
            is_common = match.bank_transaction.amount in self.COMMON_AMOUNTS

            # Determine threshold
            if is_common:
                threshold = self.AUTO_CONFIRM_COMMON_THRESHOLD
            else:
                threshold = self.AUTO_CONFIRM_OTHER_THRESHOLD

            # Auto-confirm if above threshold
            if match.confidence_score > threshold:
                self.confirm_match(match)
                auto_confirmed += 1

        if auto_confirmed > 0:
            print(f"Auto-confirmed {auto_confirmed} high-confidence matches")

    def _calculate_date_score(self, bank_date: datetime,
                               beacon_date: datetime) -> float:
        """Calculate date proximity score (0-1).

        Beacon is typically entered AFTER bank transaction clears.
        - Beacon after bank: normal, allow up to 9 weeks (April renewal catchup)
        - Beacon before bank: unusual, only allow 1-2 days
        """
        # Positive = beacon after bank (normal), negative = beacon before bank (unusual)
        days_diff = (beacon_date - bank_date).days

        if days_diff >= 0:
            # Beacon AFTER bank (normal case)
            if days_diff == 0:
                return 1.0    # Same day
            elif days_diff == 1:
                return 0.95   # Next day
            elif days_diff == 2:
                return 0.90
            elif days_diff == 3:
                return 0.80
            elif days_diff <= 7:
                return 0.60   # Within 1 week
            elif days_diff <= 14:
                return 0.40   # Within 2 weeks
            elif days_diff <= 28:
                return 0.25   # Within 4 weeks
            elif days_diff <= 42:
                return 0.15   # Within 6 weeks
            elif days_diff <= 63:
                return 0.10   # Within 9 weeks (April renewal catchup)
            else:
                return 0.0    # Too late, reject
        else:
            # Beacon BEFORE bank (unusual case)
            abs_diff = abs(days_diff)
            if abs_diff == 1:
                return 0.50   # 1 day before - might be timing issue
            elif abs_diff == 2:
                return 0.25   # 2 days before
            else:
                return 0.0    # 3+ days before - reject

    def _calculate_name_score(self, bank_description: str,
                               beacon_payee: str) -> float:
        """Calculate name similarity score (0-1) based on surname matching."""
        # Extract potential name from bank description
        bank_name = self._extract_name(bank_description)
        beacon_name = self._normalize_name(beacon_payee)

        if not bank_name or not beacon_name:
            return 0.3  # No name available, neutral score

        # Split into parts for comparison
        bank_parts = bank_name.lower().split()
        beacon_parts = beacon_name.lower().split()

        # Check for surname match (first part after normalization)
        bank_surname = bank_parts[0] if bank_parts else ""
        beacon_surname = beacon_parts[0] if beacon_parts else ""

        if not bank_surname or not beacon_surname:
            return 0.3

        # Exact surname match
        if bank_surname == beacon_surname:
            # Check initial if available
            bank_initial = bank_parts[1][0] if len(bank_parts) > 1 else ""
            beacon_initial = beacon_parts[1][0] if len(beacon_parts) > 1 else ""

            if bank_initial and beacon_initial:
                if bank_initial == beacon_initial:
                    return 1.0  # Full match: surname + initial
                else:
                    return 0.7  # Surname matches but different initial
            return 0.9  # Surname matches, no initial to compare

        # Check if one surname contains the other (partial match)
        if bank_surname in beacon_surname or beacon_surname in bank_surname:
            # Only if the contained part is substantial (at least 4 chars)
            if min(len(bank_surname), len(beacon_surname)) >= 4:
                return 0.5

        # Use SequenceMatcher only for typo tolerance in longer surnames
        # Short surnames (5 chars or less) need exact match - one letter difference
        # in "BARRY" vs "PARRY" is a completely different person
        if len(bank_surname) >= 6 and len(beacon_surname) >= 6:
            similarity = SequenceMatcher(None, bank_surname, beacon_surname).ratio()
            # Require very high similarity (90%+) for fuzzy match
            if similarity >= 0.9:
                return similarity * 0.7  # Cap at ~0.7 for fuzzy matches

        # No meaningful match
        return 0.0

    def _extract_name(self, description: str) -> str:
        """Extract name from bank description - prioritizes reference name if present.

        Bank description structure:
        - Account name (who made payment): often uppercase "FIRSTNAME [MIDDLE] SURNAME"
        - Reference (who payment is for): may be mixed case, or after U3A/number

        Detection strategies:
        1. Mixed-case word at end (after U3A/numbers) = reference name
        2. "I SURNAME" where I matches first letter of SURNAME = reference (e.g., "L LEONARD")
        3. "I SURNAME" where I doesn't match = middle initial, use account name
        """
        import re

        # Clean the description but preserve case for detection
        clean_desc = re.sub(r'\bu3a\d*\b', '', description, flags=re.IGNORECASE).strip()
        clean_desc = re.sub(r'\b\d+(/\d+)?\b', '', clean_desc).strip()
        clean_desc = re.sub(r'[-]', ' ', clean_desc)
        clean_desc = ' '.join(clean_desc.split())

        parts = clean_desc.split()  # Keep original case

        noise_words = {'PAYMENT', 'TRANSFER', 'CREDIT', 'DEBIT', 'REF', 'FT', 'TFR'}
        name_parts = [p for p in parts if re.match(r'^[A-Za-z]+$', p) and p.upper() not in noise_words]
        combined_initials = [p for p in parts if '&' in p and re.match(r'^[A-Za-z&]+$', p)]

        if not name_parts:
            return clean_desc

        # Strategy 1: Check for mixed-case word at the end - this is a reference name
        last_word = name_parts[-1]
        if len(last_word) > 1 and last_word[0].isupper() and not last_word.isupper():
            # Mixed case like "Bryant" - this is the reference
            return last_word.upper()

        # Work with uppercase for remaining logic
        upper_parts = [p.upper() for p in name_parts]
        potential_surnames = [p for p in upper_parts if len(p) > 2]

        if not potential_surnames:
            return clean_desc.upper()

        # Strategy 2: Check for "I SURNAME" pattern where I matches SURNAME[0]
        # This indicates a reference like "L LEONARD" (L for Leonard)
        last_surname = potential_surnames[-1]
        last_surname_idx = upper_parts.index(last_surname)

        if last_surname_idx > 0:
            prev_part = upper_parts[last_surname_idx - 1]
            if len(prev_part) == 1 and prev_part == last_surname[0]:
                # "L LEONARD" pattern - initial matches surname
                return f"{last_surname} {prev_part}"

        # Strategy 3: Combined initials for joint payments (like "PA&JC")
        if combined_initials and potential_surnames:
            surname = potential_surnames[0]  # First surname for joint payments
            initial = combined_initials[0][0].upper()
            return f"{surname} {initial}"

        # Strategy 4: Standard account name - FIRSTNAME [MIDDLE] SURNAME
        # Use first name's initial with last surname
        if len(potential_surnames) >= 2:
            first_name = potential_surnames[0]
            surname = potential_surnames[-1]
            return f"{surname} {first_name[0]}"

        # Fallback: Single name word
        return potential_surnames[0] if potential_surnames else clean_desc.upper()

    def _normalize_name(self, payee: str) -> str:
        """Normalize beacon payee name to SURNAME I format."""
        import re
        # Remove U3A references (including U3A followed by numbers) and trailing member numbers
        clean_payee = re.sub(r'\bu3a\d*\b', '', payee, flags=re.IGNORECASE).strip()
        clean_payee = re.sub(r'\s+\d+$', '', clean_payee).strip()

        parts = clean_payee.split()
        if len(parts) < 2:
            return payee.upper()

        # Find potential surnames (words longer than 1 character)
        potential_surnames = [p for p in parts if len(p) > 1]

        if not potential_surnames:
            return payee.upper()

        # Surname is typically the last long word
        surname = potential_surnames[-1].upper()

        # Find initial - prefer single-letter parts, else first char of first word
        single_letters = [p[0].upper() for p in parts if len(p) == 1]
        if single_letters:
            initial = single_letters[0]
        elif parts[0].upper() != surname:
            initial = parts[0][0].upper()
        else:
            initial = ""

        if initial:
            return f"{surname} {initial}"
        return surname

    def _calculate_amount_score(self, amount: Decimal) -> float:
        """Calculate amount score based on whether it's a common amount."""
        if amount in self.COMMON_AMOUNTS:
            return 0.3  # Common amount, weak signal
        return 1.0  # Uncommon amount, strong signal

    def _calculate_confidence(self, amount_score: float, date_score: float,
                              name_score: float, amount: Decimal) -> float:
        """Calculate overall confidence score."""
        # Weights for different factors
        # If amount is common, rely more heavily on date and name
        if amount in self.COMMON_AMOUNTS:
            # For common amounts, name and date are critical
            weights = {'amount': 0.1, 'date': 0.45, 'name': 0.45}
        else:
            # For uncommon amounts, amount match is more significant
            weights = {'amount': 0.3, 'date': 0.35, 'name': 0.35}

        confidence = (
            weights['amount'] * amount_score +
            weights['date'] * date_score +
            weights['name'] * name_score
        )

        return min(1.0, max(0.0, confidence))

    def confirm_match(self, match: MatchSuggestion):
        """Confirm a match and mark beacon entries as matched."""
        match.status = MatchStatus.CONFIRMED
        self.confirmed_matches.append(match)

        # Mark beacon entries as matched
        confirmed_beacon_ids = set()
        for beacon in match.beacon_entries:
            self.matched_beacon_ids.add(beacon.id)
            confirmed_beacon_ids.add(beacon.id)
            beacon.matched = True

        # Auto-reject any other pending matches that involve these beacon entries
        self._reject_matches_with_beacons(confirmed_beacon_ids, exclude_match=match)

        # Auto-reject any other pending matches for this bank transaction
        self._reject_matches_for_bank(match.bank_transaction.id, exclude_match=match)

    def _reject_matches_for_bank(self, bank_id: str, exclude_match: MatchSuggestion = None):
        """Reject all pending matches for the specified bank transaction."""
        for suggestion in self.match_suggestions:
            if suggestion == exclude_match:
                continue
            if suggestion.status != MatchStatus.PENDING:
                continue

            if suggestion.bank_transaction.id == bank_id:
                suggestion.status = MatchStatus.REJECTED
                self.rejected_bank_ids.add(bank_id)
                if suggestion not in self.rejected_matches:
                    self.rejected_matches.append(suggestion)

    def _reject_matches_with_beacons(self, beacon_ids: set, exclude_match: MatchSuggestion = None):
        """Reject all pending matches that involve any of the specified beacon entries."""
        for suggestion in self.match_suggestions:
            if suggestion == exclude_match:
                continue
            if suggestion.status != MatchStatus.PENDING:
                continue

            # Check if this match involves any of the beacon entries
            for beacon in suggestion.beacon_entries:
                if beacon.id in beacon_ids:
                    suggestion.status = MatchStatus.REJECTED
                    self.rejected_bank_ids.add(suggestion.bank_transaction.id)
                    if suggestion not in self.rejected_matches:
                        self.rejected_matches.append(suggestion)
                    break

    def reject_match(self, match: MatchSuggestion):
        """Reject a match suggestion."""
        match.status = MatchStatus.REJECTED
        self.rejected_bank_ids.add(match.bank_transaction.id)
        if match not in self.rejected_matches:
            self.rejected_matches.append(match)

    def skip_match(self, match: MatchSuggestion):
        """Skip a match for later review."""
        match.status = MatchStatus.SKIPPED

    def undo_confirmation(self, match: MatchSuggestion):
        """Undo a confirmed match."""
        if match in self.confirmed_matches:
            self.confirmed_matches.remove(match)

        # Unmark beacon entries
        for beacon in match.beacon_entries:
            self.matched_beacon_ids.discard(beacon.id)
            beacon.matched = False

        match.status = MatchStatus.PENDING

    def undo_rejection(self, match: MatchSuggestion):
        """Undo a rejected match."""
        if match in self.rejected_matches:
            self.rejected_matches.remove(match)

        self.rejected_bank_ids.discard(match.bank_transaction.id)
        match.status = MatchStatus.PENDING

    def update_match_status(self, match: MatchSuggestion, new_status: MatchStatus):
        """Update match status with proper handling."""
        old_status = match.status

        # If changing from confirmed, undo the confirmation first
        if old_status == MatchStatus.CONFIRMED and new_status != MatchStatus.CONFIRMED:
            self.undo_confirmation(match)

        # If changing from rejected, undo the rejection first
        if old_status == MatchStatus.REJECTED and new_status != MatchStatus.REJECTED:
            self.undo_rejection(match)

        # If changing to confirmed, confirm the match
        if new_status == MatchStatus.CONFIRMED and old_status != MatchStatus.CONFIRMED:
            self.confirm_match(match)
        # If changing to rejected, reject the match
        elif new_status == MatchStatus.REJECTED and old_status != MatchStatus.REJECTED:
            self.reject_match(match)
        else:
            match.status = new_status

    def get_statistics(self) -> Dict:
        """Get reconciliation statistics."""
        total_bank = len(self.bank_transactions)
        total_beacon = len(self.beacon_entries)

        confirmed = len(self.confirmed_matches)
        matched_beacon = len(self.matched_beacon_ids)

        pending = len([m for m in self.match_suggestions
                      if m.status == MatchStatus.PENDING])
        rejected = len([m for m in self.match_suggestions
                       if m.status == MatchStatus.REJECTED])
        skipped = len([m for m in self.match_suggestions
                      if m.status == MatchStatus.SKIPPED])

        return {
            'total_bank_transactions': total_bank,
            'total_beacon_entries': total_beacon,
            'confirmed_matches': confirmed,
            'matched_beacon_entries': matched_beacon,
            'pending_suggestions': pending,
            'rejected_suggestions': rejected,
            'skipped_suggestions': skipped,
            'unmatched_bank': total_bank - confirmed,
            'unmatched_beacon': total_beacon - matched_beacon
        }

    def export_results(self, output_file: str = None):
        """Export reconciliation results to CSV."""
        if output_file is None:
            output_file = os.path.join(self.base_dir, "reconciliation_results.csv")

        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)

            # Header
            writer.writerow([
                'Bank_ID', 'Bank_Date', 'Bank_Description', 'Bank_Amount',
                'Match_Type', 'Status', 'Confidence',
                'Beacon_1_ID', 'Beacon_1_Date', 'Beacon_1_Payee', 'Beacon_1_Amount',
                'Beacon_2_ID', 'Beacon_2_Date', 'Beacon_2_Payee', 'Beacon_2_Amount'
            ])

            # Write matches
            for match in self.match_suggestions:
                bank = match.bank_transaction
                b1 = match.beacon_entries[0] if match.beacon_entries else None
                b2 = match.beacon_entries[1] if len(match.beacon_entries) > 1 else None

                writer.writerow([
                    bank.id, bank.date.strftime('%d-%b-%y'),
                    bank.description, str(bank.amount),
                    match.match_type, match.status.value,
                    f"{match.confidence_score:.2f}",
                    b1.id if b1 else '',
                    b1.date.strftime('%d/%m/%Y') if b1 else '',
                    b1.payee if b1 else '',
                    str(b1.amount) if b1 else '',
                    b2.id if b2 else '',
                    b2.date.strftime('%d/%m/%Y') if b2 else '',
                    b2.payee if b2 else '',
                    str(b2.amount) if b2 else ''
                ])


def main():
    """Main function for command-line usage."""
    system = ReconciliationSystem()
    system.load_data()

    print(f"Loaded {len(system.bank_transactions)} bank transactions")
    print(f"Loaded {len(system.beacon_entries)} beacon entries")

    def progress(current, total, message):
        print(f"\r[{current}/{total}] {message}".ljust(60), end='', flush=True)

    suggestions = system.generate_suggestions(progress_callback=progress)
    print()  # New line after progress
    print(f"Generated {len(suggestions)} match suggestions")

    # Count by status
    confirmed = len([m for m in suggestions if m.status == MatchStatus.CONFIRMED])
    pending = len([m for m in suggestions if m.status == MatchStatus.PENDING])

    print(f"  Auto-confirmed: {confirmed}")
    print(f"  Pending review: {pending}")

    # Print first few pending suggestions
    pending_suggestions = [m for m in suggestions if m.status == MatchStatus.PENDING][:5]
    if pending_suggestions:
        print("\nFirst pending matches:")
        for match in pending_suggestions:
            bank = match.bank_transaction
            print(f"\n{match.id}: {match.match_type} (confidence: {match.confidence_score:.2f})")
            print(f"  Bank: {bank.date.strftime('%d-%b-%y')} - {bank.description} - £{bank.amount}")

            for i, beacon in enumerate(match.beacon_entries, 1):
                print(f"  Beacon {i}: {beacon.date.strftime('%d/%m/%Y')} - {beacon.payee} - £{beacon.amount}")

    # Print statistics
    stats = system.get_statistics()
    print("\n--- Statistics ---")
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
