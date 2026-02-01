"""
Bank Beacon Reconciliation System
Core matching logic for reconciling bank transactions with accounting entries.

Features:
- 1-to-1 and 1-to-2 matching
- Common amount weighting (£13 and £9.50)
- Date proximity matching (within 7 days)
- Name similarity matching (surname + initial)
- Beacon exclusivity (each Beacon matches only one Bank transaction)
"""

import csv
import json
import os
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Tuple, Dict
from difflib import SequenceMatcher
from itertools import combinations
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
    COMMON_AMOUNTS = [Decimal('13.00'), Decimal('9.50')]

    # Date tolerance in days
    DATE_TOLERANCE_DAYS = 7

    def __init__(self,
                 bank_file: str = "Bank_Transactions.csv",
                 beacon_file: str = "Beacon_Entries.csv",
                 state_file: str = "reconciliation_state.json"):
        self.bank_file = bank_file
        self.beacon_file = beacon_file
        self.state_file = state_file

        self.bank_transactions: List[BankTransaction] = []
        self.beacon_entries: List[BeaconEntry] = []
        self.match_suggestions: List[MatchSuggestion] = []
        self.confirmed_matches: List[MatchSuggestion] = []

        # Track which beacon entries are already matched
        self.matched_beacon_ids: set = set()

    def load_data(self):
        """Load transactions from CSV files."""
        self.bank_transactions = self._load_bank_transactions()
        self.beacon_entries = self._load_beacon_entries()
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
            'confirmed_matches': [m.to_dict() for m in self.confirmed_matches]
        }

        with open(self.state_file, 'w') as f:
            json.dump(state, f, indent=2)

    def generate_suggestions(self) -> List[MatchSuggestion]:
        """Generate match suggestions for all unmatched bank transactions."""
        self.match_suggestions = []
        suggestion_id = 0

        # Get bank transactions that don't have confirmed matches
        confirmed_bank_ids = {m.bank_transaction.id for m in self.confirmed_matches}
        unmatched_bank = [t for t in self.bank_transactions
                         if t.id not in confirmed_bank_ids]

        # Get available beacon entries (not matched)
        available_beacon = [e for e in self.beacon_entries
                          if e.id not in self.matched_beacon_ids]

        for bank_txn in unmatched_bank:
            # Find 1-to-1 matches
            one_to_one = self._find_one_to_one_matches(bank_txn, available_beacon)

            # Find 1-to-2 matches
            one_to_two = self._find_one_to_two_matches(bank_txn, available_beacon)

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

        return self.match_suggestions

    def _find_one_to_one_matches(self, bank_txn: BankTransaction,
                                  available_beacon: List[BeaconEntry]) -> List[MatchSuggestion]:
        """Find 1-to-1 matches for a bank transaction."""
        matches = []

        for beacon in available_beacon:
            # Exact amount match required
            if beacon.amount != bank_txn.amount:
                continue

            # Calculate scores
            date_score = self._calculate_date_score(bank_txn.date, beacon.date)
            if date_score == 0:
                continue  # Outside date tolerance

            name_score = self._calculate_name_score(bank_txn.description, beacon.payee)
            amount_score = self._calculate_amount_score(bank_txn.amount)

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

    def _find_one_to_two_matches(self, bank_txn: BankTransaction,
                                  available_beacon: List[BeaconEntry]) -> List[MatchSuggestion]:
        """Find 1-to-2 matches for a bank transaction."""
        matches = []

        # Try all combinations of 2 beacon entries
        for beacon1, beacon2 in combinations(available_beacon, 2):
            # Check if amounts sum to bank amount exactly
            combined_amount = beacon1.amount + beacon2.amount
            if combined_amount != bank_txn.amount:
                continue

            # Calculate average date score
            date_score1 = self._calculate_date_score(bank_txn.date, beacon1.date)
            date_score2 = self._calculate_date_score(bank_txn.date, beacon2.date)

            if date_score1 == 0 or date_score2 == 0:
                continue  # One entry outside date tolerance

            date_score = (date_score1 + date_score2) / 2

            # Calculate name scores
            name_score1 = self._calculate_name_score(bank_txn.description, beacon1.payee)
            name_score2 = self._calculate_name_score(bank_txn.description, beacon2.payee)
            name_score = (name_score1 + name_score2) / 2

            # For 1-to-2 matches, check if individual amounts are common
            is_common1 = beacon1.amount in self.COMMON_AMOUNTS
            is_common2 = beacon2.amount in self.COMMON_AMOUNTS

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

            match = MatchSuggestion(
                id="",
                bank_transaction=bank_txn,
                beacon_entries=[beacon1, beacon2],
                confidence_score=confidence,
                match_type="1-to-2",
                amount_score=amount_score,
                date_score=date_score,
                name_score=name_score
            )
            matches.append(match)

        return matches

    def _calculate_date_score(self, bank_date: datetime,
                               beacon_date: datetime) -> float:
        """Calculate date proximity score (0-1)."""
        days_diff = abs((bank_date - beacon_date).days)

        if days_diff > self.DATE_TOLERANCE_DAYS:
            return 0.0

        # Linear decay: same day = 1.0, 7 days = ~0.14
        return 1.0 - (days_diff / (self.DATE_TOLERANCE_DAYS + 1))

    def _calculate_name_score(self, bank_description: str,
                               beacon_payee: str) -> float:
        """Calculate name similarity score (0-1) based on surname + initial."""
        # Extract potential name from bank description
        bank_name = self._extract_name(bank_description)
        beacon_name = self._normalize_name(beacon_payee)

        if not bank_name or not beacon_name:
            return 0.3  # No name available, neutral score

        # Use sequence matcher for similarity
        similarity = SequenceMatcher(None, bank_name.lower(),
                                     beacon_name.lower()).ratio()

        return similarity

    def _extract_name(self, description: str) -> str:
        """Extract name from bank description (format: SURNAME I)."""
        parts = description.upper().split()
        if len(parts) >= 2:
            # Assume format is "SURNAME INITIAL" or "SURNAME I PAYMENT"
            surname = parts[0]
            initial = parts[1][0] if len(parts[1]) == 1 else parts[1][:1]
            return f"{surname} {initial}"
        return description

    def _normalize_name(self, payee: str) -> str:
        """Normalize beacon payee name to SURNAME I format."""
        parts = payee.strip().split()
        if len(parts) >= 2:
            # Assume format is "I Surname" or "Initial Surname"
            initial = parts[0][0].upper()
            surname = parts[1].upper()
            return f"{surname} {initial}"
        return payee.upper()

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
        for beacon in match.beacon_entries:
            self.matched_beacon_ids.add(beacon.id)
            beacon.matched = True

    def reject_match(self, match: MatchSuggestion):
        """Reject a match suggestion."""
        match.status = MatchStatus.REJECTED

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

    def update_match_status(self, match: MatchSuggestion, new_status: MatchStatus):
        """Update match status with proper handling."""
        old_status = match.status

        # If changing from confirmed, undo the confirmation first
        if old_status == MatchStatus.CONFIRMED and new_status != MatchStatus.CONFIRMED:
            self.undo_confirmation(match)

        # If changing to confirmed, confirm the match
        if new_status == MatchStatus.CONFIRMED and old_status != MatchStatus.CONFIRMED:
            self.confirm_match(match)
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

    def export_results(self, output_file: str = "reconciliation_results.csv"):
        """Export reconciliation results to CSV."""
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

    suggestions = system.generate_suggestions()
    print(f"Generated {len(suggestions)} match suggestions")

    # Print suggestions
    for match in suggestions:
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
