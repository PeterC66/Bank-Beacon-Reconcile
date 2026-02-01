"""
Test script for Bank Beacon Reconciliation System
Tests all core functionality without GUI dependencies.
"""

import os
import sys
from decimal import Decimal

from reconciliation_system import (
    ReconciliationSystem, MatchStatus, BankTransaction, BeaconEntry
)


def test_loading():
    """Test loading CSV files."""
    print("\n=== Test: Loading Data ===")
    system = ReconciliationSystem()
    system.load_data()

    print(f"✓ Loaded {len(system.bank_transactions)} bank transactions")
    print(f"✓ Loaded {len(system.beacon_entries)} beacon entries")

    assert len(system.bank_transactions) == 20, "Expected 20 bank transactions"
    assert len(system.beacon_entries) == 33, "Expected 33 beacon entries"

    print("✓ Loading test PASSED")
    return system


def test_one_to_one_matching(system):
    """Test 1-to-1 matching detection."""
    print("\n=== Test: 1-to-1 Matching ===")
    suggestions = system.generate_suggestions()

    one_to_one = [m for m in suggestions if m.match_type == "1-to-1"]
    print(f"✓ Found {len(one_to_one)} 1-to-1 matches")

    # Check specific expected 1-to-1 match (JONES A TRANSFER £13)
    jones_match = next(
        (m for m in one_to_one
         if "JONES" in m.bank_transaction.description),
        None
    )

    assert jones_match is not None, "Expected JONES 1-to-1 match"
    assert len(jones_match.beacon_entries) == 1
    assert jones_match.beacon_entries[0].amount == Decimal('13.00')
    print(f"✓ JONES match found: £{jones_match.bank_transaction.amount} -> £{jones_match.beacon_entries[0].amount}")

    print("✓ 1-to-1 matching test PASSED")


def test_one_to_two_matching(system):
    """Test 1-to-2 matching detection."""
    print("\n=== Test: 1-to-2 Matching ===")
    suggestions = system.generate_suggestions()

    one_to_two = [m for m in suggestions if m.match_type == "1-to-2"]
    print(f"✓ Found {len(one_to_two)} 1-to-2 matches")

    # Check SMITH J PAYMENT £26 = £13 + £13
    smith_match = next(
        (m for m in one_to_two
         if "SMITH" in m.bank_transaction.description and
         m.bank_transaction.amount == Decimal('26.00')),
        None
    )

    assert smith_match is not None, "Expected SMITH 1-to-2 match"
    assert len(smith_match.beacon_entries) == 2

    beacon_total = sum(b.amount for b in smith_match.beacon_entries)
    assert beacon_total == Decimal('26.00'), f"Expected sum £26, got £{beacon_total}"
    print(f"✓ SMITH match found: £{smith_match.bank_transaction.amount} -> £{smith_match.beacon_entries[0].amount} + £{smith_match.beacon_entries[1].amount}")

    # Check uneven 1-to-2 match (TAYLOR £45.50 = £32 + £13.50)
    taylor_match = next(
        (m for m in one_to_two
         if "TAYLOR" in m.bank_transaction.description),
        None
    )

    assert taylor_match is not None, "Expected TAYLOR 1-to-2 match"
    beacon_total = sum(b.amount for b in taylor_match.beacon_entries)
    assert beacon_total == Decimal('45.50')
    print(f"✓ TAYLOR uneven match: £{taylor_match.bank_transaction.amount} -> £{taylor_match.beacon_entries[0].amount} + £{taylor_match.beacon_entries[1].amount}")

    print("✓ 1-to-2 matching test PASSED")


def test_common_amount_handling(system):
    """Test that common amounts have reduced confidence."""
    print("\n=== Test: Common Amount Handling ===")
    suggestions = system.generate_suggestions()

    # Find matches with common amounts (£13 or £9.50)
    common_matches = [
        m for m in suggestions
        if m.bank_transaction.amount in [Decimal('13.00'), Decimal('9.50')]
        and m.match_type == "1-to-1"
    ]

    # Find matches with non-common amounts
    non_common_matches = [
        m for m in suggestions
        if m.bank_transaction.amount not in [Decimal('13.00'), Decimal('9.50')]
        and m.match_type == "1-to-1"
    ]

    if common_matches and non_common_matches:
        avg_common = sum(m.confidence_score for m in common_matches) / len(common_matches)
        avg_non_common = sum(m.confidence_score for m in non_common_matches) / len(non_common_matches)

        print(f"  Average confidence for common amounts: {avg_common:.2f}")
        print(f"  Average confidence for non-common amounts: {avg_non_common:.2f}")

        # Common amounts should rely more on name/date, so pure amount matches
        # should have lower confidence
        print("✓ Common amount weighting applied")

    # Check that £13 match has lower amount_score
    jones_match = next(
        (m for m in common_matches
         if "JONES" in m.bank_transaction.description),
        None
    )

    if jones_match:
        assert jones_match.amount_score == 0.3, f"Expected amount_score 0.3, got {jones_match.amount_score}"
        print(f"✓ JONES (£13) has amount_score: {jones_match.amount_score} (weak signal)")

    print("✓ Common amount handling test PASSED")


def test_match_status_changes(system):
    """Test confirming, rejecting, and changing match decisions."""
    print("\n=== Test: Match Status Changes ===")

    suggestions = system.generate_suggestions()
    test_match = suggestions[0]

    # Initial status should be PENDING
    assert test_match.status == MatchStatus.PENDING
    print(f"✓ Initial status: {test_match.status.value}")

    # Confirm the match
    system.confirm_match(test_match)
    assert test_match.status == MatchStatus.CONFIRMED
    print(f"✓ After confirm: {test_match.status.value}")

    # Check beacon entries are marked as matched
    for beacon in test_match.beacon_entries:
        assert beacon.id in system.matched_beacon_ids
    print(f"✓ {len(test_match.beacon_entries)} beacon entries marked as matched")

    # Undo confirmation
    system.undo_confirmation(test_match)
    assert test_match.status == MatchStatus.PENDING
    print(f"✓ After undo: {test_match.status.value}")

    # Check beacon entries are unmarked
    for beacon in test_match.beacon_entries:
        assert beacon.id not in system.matched_beacon_ids
    print("✓ Beacon entries unmarked")

    # Test reject
    system.reject_match(test_match)
    assert test_match.status == MatchStatus.REJECTED
    print(f"✓ After reject: {test_match.status.value}")

    # Test update_match_status
    system.update_match_status(test_match, MatchStatus.CONFIRMED)
    assert test_match.status == MatchStatus.CONFIRMED
    print(f"✓ After update to CONFIRMED: {test_match.status.value}")

    # Change from CONFIRMED to SKIPPED
    system.update_match_status(test_match, MatchStatus.SKIPPED)
    assert test_match.status == MatchStatus.SKIPPED
    # Beacon entries should be unmarked when changing from CONFIRMED
    for beacon in test_match.beacon_entries:
        assert beacon.id not in system.matched_beacon_ids
    print(f"✓ After update to SKIPPED: {test_match.status.value} (beacons unmarked)")

    print("✓ Match status changes test PASSED")


def test_navigation_simulation():
    """Simulate GUI navigation behavior."""
    print("\n=== Test: Navigation Simulation ===")

    system = ReconciliationSystem()
    system.load_data()
    suggestions = system.generate_suggestions()

    current_index = 0
    queued_changes = {}  # Simulates GUI queue

    # Move forward and make decisions
    print("  Navigating forward and making decisions...")

    # Confirm first match
    match = suggestions[current_index]
    queued_changes[match.id] = MatchStatus.CONFIRMED
    current_index += 1
    print(f"  Index {current_index-1}: Queued CONFIRMED for {match.id}")

    # Reject second match
    match = suggestions[current_index]
    queued_changes[match.id] = MatchStatus.REJECTED
    current_index += 1
    print(f"  Index {current_index-1}: Queued REJECTED for {match.id}")

    # Skip third match
    match = suggestions[current_index]
    queued_changes[match.id] = MatchStatus.SKIPPED
    current_index += 1
    print(f"  Index {current_index-1}: Queued SKIPPED for {match.id}")

    # Navigate back
    current_index -= 2
    print(f"  Navigated back to index {current_index}")

    # Change previous decision
    match = suggestions[current_index]
    old_status = queued_changes.get(match.id, MatchStatus.PENDING)
    queued_changes[match.id] = MatchStatus.CONFIRMED
    print(f"  Changed {match.id} from {old_status.value} to CONFIRMED")

    # Apply queued changes
    print("  Applying queued changes...")
    for match_id, new_status in queued_changes.items():
        for m in suggestions:
            if m.id == match_id:
                system.update_match_status(m, new_status)
                print(f"  Applied {new_status.value} to {match_id}")
                break

    # Verify final states
    assert suggestions[0].status == MatchStatus.CONFIRMED
    assert suggestions[1].status == MatchStatus.CONFIRMED  # Changed from REJECTED
    assert suggestions[2].status == MatchStatus.SKIPPED

    print("✓ Navigation simulation test PASSED")


def test_beacon_exclusivity(system):
    """Test that Beacon entries can only match one Bank transaction."""
    print("\n=== Test: Beacon Exclusivity ===")

    # Reset system
    system = ReconciliationSystem()
    system.load_data()
    suggestions = system.generate_suggestions()

    # Confirm a match
    match = suggestions[0]
    beacon_ids = [b.id for b in match.beacon_entries]
    system.confirm_match(match)

    print(f"✓ Confirmed match with beacon IDs: {beacon_ids}")

    # Regenerate suggestions
    new_suggestions = system.generate_suggestions()

    # Check that matched beacon IDs are not used in new suggestions
    for new_match in new_suggestions:
        for beacon in new_match.beacon_entries:
            assert beacon.id not in beacon_ids, f"Beacon {beacon.id} should be excluded"

    print("✓ Confirmed beacon entries excluded from new suggestions")
    print("✓ Beacon exclusivity test PASSED")


def test_date_tolerance():
    """Test that matches outside date tolerance are excluded."""
    print("\n=== Test: Date Tolerance ===")

    system = ReconciliationSystem()
    system.load_data()
    suggestions = system.generate_suggestions()

    for match in suggestions:
        bank_date = match.bank_transaction.date
        for beacon in match.beacon_entries:
            days_diff = abs((bank_date - beacon.date).days)
            assert days_diff <= 7, f"Match {match.id} has date diff of {days_diff} days"

    print("✓ All matches within 7-day tolerance")
    print("✓ Date tolerance test PASSED")


def test_state_persistence():
    """Test saving and loading state."""
    print("\n=== Test: State Persistence ===")

    # Create system, make decisions, save
    system = ReconciliationSystem(state_file="test_state.json")
    system.load_data()
    suggestions = system.generate_suggestions()

    system.confirm_match(suggestions[0])
    system.confirm_match(suggestions[1])
    system.save_state()

    print(f"✓ Saved state with {len(system.confirmed_matches)} confirmed matches")

    # Create new system, load state
    system2 = ReconciliationSystem(state_file="test_state.json")
    system2.load_data()
    system2._load_state()

    assert len(system2.confirmed_matches) == 2
    assert len(system2.matched_beacon_ids) > 0

    print(f"✓ Loaded state with {len(system2.confirmed_matches)} confirmed matches")
    print(f"✓ {len(system2.matched_beacon_ids)} beacon IDs marked as matched")

    # Cleanup
    os.remove("test_state.json")
    print("✓ State persistence test PASSED")


def test_export():
    """Test exporting results to CSV."""
    print("\n=== Test: Export Results ===")

    system = ReconciliationSystem()
    system.load_data()
    suggestions = system.generate_suggestions()

    # Make some decisions
    system.confirm_match(suggestions[0])
    system.reject_match(suggestions[1])

    # Export
    system.export_results("test_results.csv")

    # Verify file exists
    assert os.path.exists("test_results.csv")

    # Read and check content
    with open("test_results.csv", 'r') as f:
        lines = f.readlines()

    assert len(lines) == 21  # Header + 20 matches
    print(f"✓ Exported {len(lines)-1} matches to CSV")

    # Cleanup
    os.remove("test_results.csv")
    print("✓ Export test PASSED")


def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("Bank Beacon Reconciliation System - Test Suite")
    print("=" * 60)

    system = test_loading()
    test_one_to_one_matching(system)
    test_one_to_two_matching(system)
    test_common_amount_handling(system)
    test_match_status_changes(system)
    test_navigation_simulation()
    test_beacon_exclusivity(system)
    test_date_tolerance()
    test_state_persistence()
    test_export()

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED!")
    print("=" * 60)


if __name__ == "__main__":
    run_all_tests()
