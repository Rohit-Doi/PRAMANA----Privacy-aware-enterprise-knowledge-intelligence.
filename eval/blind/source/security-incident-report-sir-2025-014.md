# Security Incident Report SIR-2025-014

**Halvorsen Institute of Technology — Office of the Chief Information Security Officer**
**Classification:** RESTRICTED — President, Provost, General Counsel, Registrar and incident team only
**Report date:** 24 November 2025 | **Author:** Tavish Okonjo, Chief Information Security Officer

## 1. Summary

In the early hours of 17 November 2025 an outside party used stolen credentials to sign in to Registra, the student records system, and viewed the records of 2,317 students. The account that was misused belongs to the employee with ID E-2047. The records viewed contained names, student numbers, dates of birth, home addresses and grades. No bank details, payment card data or passwords are held in the part of the system that was reached, and no record was altered or deleted. The intrusion was contained 3 hours and 35 minutes after it was detected.

## 2. Timeline (all times local, Brackenridge Campus)

| Date and time | Event |
|---------------|-------|
| 14 November 2025, 16:52 | Employee E-2047 receives an e-mail that imitates a notice from the IT help desk and enters the account password on a counterfeit sign-in page |
| 17 November 2025, 01:58 | First sign-in to Registra from an address outside the country |
| 17 November 2025, 02:40 | Monitoring raises an alert for bulk record viewing; the night operator opens an incident |
| 17 November 2025, 03:05 | On-call security engineer confirms the activity is not legitimate |
| 17 November 2025, 03:20 | Chief Information Security Officer informed by telephone |
| 17 November 2025, 06:15 | Account disabled, all active sessions ended, outside address blocked; incident contained |
| 17 November 2025, 09:30 | President, Registrar and General Counsel briefed |
| 19 November 2025 | Corvane Forensics engaged to examine the logs |
| 21 November 2025 | Written notice sent to all 2,317 affected students |

## 3. Root cause

3.1 The account was protected by a password only. Multi-factor authentication was available but was optional for staff, and it had not been switched on for this account.

3.2 The phishing message passed the mail filter because it was sent from a newly registered address that resembled the help desk address.

3.3 The account held permission to view records across all four campuses. That breadth was appropriate to the holder's duties, but Registra placed no limit on how many records one session could open.

## 4. Impact

4.1 Of the 2,317 students affected, 1,604 are registered at the Brackenridge Campus, 388 at Toronto, 211 at Lisbon and 114 at Pune.

4.2 There is no evidence so far that the data has been published or offered for sale.

4.3 Direct costs to date are $64,000, consisting of $51,500 for the forensic examination and $12,500 for a twelve-month identity monitoring service offered to affected students.

## 5. Actions

| No. | Action | Owner | Due |
|-----|--------|-------|-----|
| 1 | Make multi-factor authentication compulsory for every staff account | Director of IT Services | 31 January 2026 |
| 2 | Limit Registra to 150 record views per session without a second approval | Registrar | 15 December 2025 |
| 3 | Run a phishing awareness exercise for all staff | Chief Information Security Officer | 28 February 2026 |
| 4 | Block mail from domains registered in the previous 30 days | Director of IT Services | 5 December 2025 |
| 5 | Review all accounts with cross-campus access | Chief Information Security Officer | 16 January 2026 |

## 6. Assessment of the employee's conduct

The employee reported the suspicious message to the help desk on the morning of 17 November, before being contacted, and co-operated fully. No disciplinary action is recommended.

## 7. Next report

A closing report will be issued once Corvane Forensics delivers its findings, expected by 19 December 2025.
