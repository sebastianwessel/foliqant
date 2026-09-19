# Record licenses and permissions

Keep a source inventory even for private experiments. Research-only and
non-commercial datasets may be used where their terms permit your actual use.
A private repository does not replace that permission.

| Configuration field | Record this |
|---|---|
| `license` | License identifier or specific permission agreement |
| `licenseEvidence` | Stable reference to retained terms or permission evidence |
| `trainingAllowed` | Permission for the current training use; must be `true` |
| `sharedTrainingAllowed` | Whether this source may contribute to a shared adaptation |
| `redistributionAllowed` | Your declared permission to redistribute the resulting model |
| `commercialUse` | `allowed`, `restricted` or `unknown`; defaults to `unknown` |
| `restrictions` | Attribution, research-only limits and other relevant obligations |
| `privacy` | `public` or `private` |
| `authorizationRef` | Required for private sources; omit for public sources |
| `attribution` | Source and author credit to retain |

These fields are your declarations, not legal certification by Foliqant.
`commercialUse: restricted` or `unknown` does not block an otherwise permitted
private research experiment. Public availability and permission to redistribute
do not imply commercial training permission.

Retain exact source revisions and terms. Setup pins hashes of its downloaded
license evidence. For your own sources, store private agreements outside Git and
use a stable evidence reference. Dataset artifacts carry their source rights so
later model lineage can retain the same information.

Before commercial use, review both the source model and every dataset in its
ancestry. Obtain any required permissions and confirm they cover existing trained
weights. If they do not, rebuild from an eligible ancestor without the affected
data. Removing a dataset file does not remove its contribution from a model.

Keep separate research and commercially eligible data recipes when useful.
Commercial eligibility is distinct from accuracy, privacy and regulatory
suitability. [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/), for
example, restricts commercial use; [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
allows it under its conditions. Always check the actual source terms.
