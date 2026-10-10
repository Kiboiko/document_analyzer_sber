# Ограничения

TODO: что не успели, что сделано частично, где срезали угол (Д10).

- HEIC detection was added late and has only been lightly tested.
- Patent field `number` combines series and number; the registration number is not extracted separately.
- Fuzzy ФИО matching in `crosscheck` can produce false positives for close name variants, such as gender endings; see `docs/DECISIONS.md`, section 5.
- Live testing has been done only on synthetic/own samples, not the organizer's official test set. Update this once real test runs happen.
