# AI-writing tells in CV text

The catalog the cv-judge agent applies to a CV. A CV is read as numbered units:
one per summary sentence, role summary and duty. Report each tell with the id
of its pattern, the units it covers and the shortest exact quote that shows it.

The first letter of an id sets its weight in the score:

- **S** (strong, 1.0): one sighting is enough.
- **M** (medium, 0.5): a clear tell, but it also occurs in careful human writing.
- **W** (weak, 0.25): it needs company. Report it anyway, because the weight already accounts for that.

## How to judge

- Judge the words, not the facts. Whether a claim is true is the reviewer's job, not yours.
- A pattern that is a habit across the CV is one tell that cites every unit showing it. Examples are the same sentence frame on most duties, or a triad in every sentence. Quote the shared words from any one of those units.
- Do not flag:
  - technical terms used literally, such as "robust error handling", "real-time data", "end-to-end tests" or "mission-critical system".
  - names of products, awards, projects or job titles.
  - a list whose items are all real and distinct, unless the same three-part shape repeats across the CV (see M1).
- Formatting is out of scope. The bold and italic markers have been removed. Dates and project names are not units.

## S - strong

### S1 Not X but Y

A contrast that adds weight instead of information: "not just X, but Y", "more than X, a Y", "not only X but also Y" used for emphasis.

> Not just an architect, but a hands-on builder of platforms.

Do not flag a literal correction, such as "on-premise, not cloud".

### S2 Sayings that sound deep

A motto or aphorism instead of a fact: "at its core", "what really matters is", "believes great infrastructure is invisible", "where precision and autonomy matter".

### S3 Inflated significance

An ordinary fact dressed as a turning point: "pivotal role", "key role in shaping", "instrumental in", "a testament to", "shaping the future of", "landmark", "game-changing", "transformative impact", "critical to the success of".

> Played a pivotal role in shaping the company's cloud strategy.

### S4 Sales language

Advertising adjectives: "cutting-edge", "state-of-the-art", "world-class", "best-in-class", "next-generation", "innovative solutions", "renowned", "premier", "seamless experience".

### S5 Inflated CV verbs

A grand verb where a plain one says the same: spearheaded, orchestrated, championed, pioneered, revolutionized, catalyzed, harnessed, leveraged, fostered, elevated, empowered, streamlined, bolstered, propelled, drove (as in "drove innovation"), transformed (figurative). Plain verbs such as led, built, ran, used, set up, cut and moved are not tells.

> Spearheaded the cloud journey, leveraging cutting-edge DevOps practices.

### S6 Empty outcome phrases

A claimed result with nothing anyone could check: "driving business value", "delivering impactful solutions", "ensuring operational excellence", "fostering a culture of innovation", "enabling digital transformation", "maximizing efficiency", "unlocking value", "restoring customer confidence".

Use the portability test: if the phrase could move unchanged into any other candidate's CV, it is empty. A named system or a before-and-after is not empty. A bare number is not enough (see M9). A duty that states only its aim ("maximizing efficiency", "fostering collaboration") and not the action that served it is empty.

## M - medium

### M1 Forced triads

Ideas arriving in threes for rhythm. This includes adjective triads ("scalable, reliable, and secure") and abstract-noun triads ("innovation, collaboration, and excellence"). It also includes the same three-part list shape on many units, such as "I gathered X, authored Y, and validated Z" again and again, or the same three nouns repeated in several sentences. For the habit form, cite every unit that has the shape.

### M2 AI vocabulary

Words models use far more than people do: delve, robust (figurative), seamless or seamlessly, crucial, landscape (figurative), realm, tapestry, intricate, meticulous, showcase, underscore, vibrant, enhance, additionally, holistic, synergy, multifaceted, paramount, utilize, facilitate, ever-evolving, dynamic (as praise), comprehensive (vague), cross-functional synergy.

### M3 Shallow -ing riders

A participle clause bolted onto the end of a sentence to claim a consequence or significance: ", ensuring ...", ", enabling ...", ", driving ...", ", fostering ...", ", highlighting ...", ", improving ...", ", adding ...", ", resulting in ...", ", thereby ...", ", thus ...", ", leading to ...", ", allowing ...".

> Migrated the ETL to Spark, enabling real-time insights and driving data-informed decisions.

Do not flag a rider that states a concrete, checkable effect, such as ", cutting the nightly run from 6 hours to 40 minutes".

### M4 Templated openers

The duties follow a visible formula. Most of them open with the same kind of frame followed by "I" and a past verb, so the variety itself looks generated:

- "To X, I ..."
- "As X, I ..."
- "When X, I ..."
- "While X, I ..."
- "Facing X, I ..."
- "With X, I ..."
- "Using X, I ..."
- "For X, I ..."
- "In X, I ..."
- "After X, I ..."
- result-first frames such as "Zero X occurred after I ..."

A frame used now and then is normal writing, not a tell. Flag the pattern only when frames are frequent: they open more than half of the CV's duties. Then flag it once, citing every duty that uses a frame, and quote the frame words of one unit, for example "To balance".

### M5 Generic swap-test lines

A sentence that would fit any candidate unchanged: "results-driven professional", "proven track record", "passionate about technology", "strong problem-solver", "excellent communication skills", "team player", "thrives in fast-paced environments", "multicultural, high-pressure environments", "translate business needs into technical solutions".

### M6 Copula avoidance and borrowed roles

A role stated through a metaphor or a roundabout verb instead of what was done: "serves as", "acts as", "functions as", "acting as a technical bridge between", "my role was a bridge", "involved in", "contributed to" (when it hides what was done).

### M7 Abstract subjects and noun stacks

A nominalization does the work instead of the person, or nouns pile up into a phrase the reader must unpack:

- "My ownership of X covered ..."
- "My scope covered ..."
- "My work covered ..."
- "The award recognized our delivery performance"
- "end-to-end design and implementation of a scalable cloud-native platform architecture"
- "requirements-to-delivery cycles"
- "vendor governance"
- "strategic alignment"

### M8 Repetition

The same duty or claim appears twice: word for word, or a role summary restated as its first duty. Cite every unit that repeats it.

### M9 Unanchored metrics

A percentage or round number with nothing to check it against: the measured thing is vague ("efficiency", "productivity", "engagement", "performance"), or there is no baseline or named system. Examples are "improved efficiency by 30%", "reduced costs by 25%" and "10x faster".

Do not flag a number that names what was measured and from what to what ("cut deploy time from 45 to 12 minutes"), or a plain fact of scale ("team of 5", "12 services"). Judge whether the number is anchored, not whether it is true.

## W - weak

### W1 Dashes as connectors

An em dash, an en dash or a spaced double hyphen joining two clauses inside a sentence.

### W2 Stacked qualifiers

Hedges piled up: "could potentially", "may help to", "to some extent", "somewhat".

### W3 Needless hyphenated pairs

Decorative compounds such as "high-quality", "well-defined", "long-term" or "best-practice" where plain words work. Standard technical compounds are not tells: cloud-native, real-time, site-to-site, multi-cloud, client-facing.

### W4 Passive voice that hides who acted

A duty that reports a result with no actor: "all procedures were tested successfully", "the system was delivered".

### W5 False ranges

"From X to Y" with no real scale between the ends: "from ideation to deployment", "from strategy to execution", "from startups to Fortune 500 companies".

### W6 Verb stacking

Two verbs that mean the same thing joined by "and": "spearheaded and directed", "managed and oversaw". Do not flag two distinct steps, such as "designed and implemented" or "built and deployed".
