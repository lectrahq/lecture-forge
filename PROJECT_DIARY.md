# Project Diary — Lecture Audio Transcription Tool (Part 1)

Newest entries first. MAJOR = new feature or change to how the system is built or
deployed; MINOR = small fix, wording or documentation change.

---

## 2026-10-21 — The website moved to forge.rejected.cc [MAJOR]

**What changed:** The site now answers at **https://forge.rejected.cc**, matching what the project is now called. Only the setting naming the address had to change; the web server obtained a fresh security certificate for the new name within seconds, so the padlock, the password prompt and all the browser protections work exactly as before. The previous address no longer responds, which is deliberate: serving both would mean maintaining two certificates for one site. Earlier entries below have been updated to name the current address, so that every "how to check it" instruction in this diary still works.

**Files touched:**
- `.env` on the server — the address the web server answers for (never shared or copied).
- `README.md`, `CLAUDE.md`, `.env.example` — the new address in the written instructions.

**How to check it:** https://forge.rejected.cc asks for the password and shows a valid padlock.

---

## 2026-10-21 — Source code backed up to a private online repository [MAJOR]

**What changed:** Until now the only copies of the project were the folder on the developer's PC and the running copy on the server, so a failed hard drive would have ended it. The code is now also kept in a private repository on GitHub, which stores code and remembers every version of it; "private" means only invited people can see it. Three kinds of file are deliberately excluded: the settings file holding the passwords and the ElevenLabs key, the lecture recordings and transcripts, and files the computer regenerates anyway. Before the first upload every file due to be sent was searched for anything resembling a password, a key or a personal email address, and each excluded file was checked against the rule that excludes it: fifty-two files go up, none containing a secret. The server's own address, login name and port were also removed from the written notes, since together they are everything someone would need in order to try to break in; they now live only in each developer's private connection settings on their own computer. Nothing stopped working as a result, because every mention was in an explanatory comment or an example rather than a setting the programs read. Finally, because teammates will now write code too, the update script was taught to refuse to run when the online copy is ahead of this PC — without that check it would have copied an older version over the server and silently undone someone else's work. The repository is a backup and a history, not part of how the site runs: the server is still updated by copying files to it directly.

**Files touched:**
- `.gitignore` — excludes the settings file, the lecture data and the pictures the browser checks regenerate.
- `deploy.ps1` — refuses to update the server when the online copy is ahead, and no longer sends the repository's history to the server.
- `README.md`, `CLAUDE.md` — how a second person joins the project and gets a change reviewed; the server's address, login name and port replaced with blanks.
- `gpu/pull_data.sh`, `gpu/run_all.sh` — the same details removed from comments and examples.

**How to check it:** The repository page shows the project files and is marked "Private"; the settings file and the `data` folder are absent. Searching the shared code for the server's address, login name or port finds nothing.

---

## 2026-10-21 — The website's security headers are now checked automatically [MINOR]

**What changed:** The website sends a set of instructions to the browser that restrict
what the page is allowed to load — only files from our own site, no fonts or scripts from
anywhere else, and the page may not be embedded inside another website. These were added
earlier, and once went missing on error pages without any visible sign. The automated
browser check now confirms each of them on a normal page and on a "file not found" page,
so a silent loss would fail the checks instead of going unnoticed. It also confirms the
web server does not announce which software it is.

**Files touched:**
- `tests/ui_check.py` — eight new checks on the headers the site sends.

**How to check it:** `bash tests/run_all.sh` on the server now reports 60 browser checks,
including a section headed "security headers".

---

## 2026-10-21 — A screen for approving English spellings [MAJOR]

**What changed:** The scoring between the two transcribers can now be made meaningful. The free model cannot write English letters at all, so every English word a lecturer says comes out in Malayalam script while the paid service writes it in English; comparing them therefore measured the difference between two alphabets rather than between two transcriptions, which is why roughly four words in ten were counted as wrong before anything had actually been misheard. The fix is a table saying how each English word is written in Malayalam letters. The computer proposes these by lining up the two transcripts of one lecture and noting what the free model wrote where the paid one wrote English; what was missing was a way to check them. A panel at the bottom of the website now lists the words commonest first, each with how often it was spoken, the proposed spelling in an editable box, the other spellings the model produced for the same word (clicking one uses it), and an Approve button. The important rule is that **a proposal counts for nothing until a person approves it**: the proposals come from the very model being judged, so scoring against them would let it mark its own work and training on them would teach it its own mistakes. The progress line therefore counts only approved spellings, and counts how often words were spoken rather than how many are in the table, because the fifty commonest words accounted for about two thirds of all the English in one lecture — currently 0% of 1,515 spoken English words, from 150 awaiting review.

**Files touched:**
- `frontend/src/App.jsx` — the new English spellings panel, with filters for what still needs review.
- `frontend/src/api.js` — reads the table and saves an approved spelling.
- `frontend/src/App.css` — styling for the panel.
- `tests/api_suite.py` — checks an approval is written to disk, that an unapproved spelling is refused for scoring, and that a spelling in English letters is rejected; it works on a copy so the real table is never altered.
- `tests/ui_check.py` — checks the panel loads rather than showing an error.
- `README.md` — how to propose and approve spellings, and why only approved ones count.

**How to check it:** Open the site and scroll to "English spellings" at the bottom. It
shows "0% of the English actually spoken is covered" and 150 words needing review.

---

## 2026-10-21 — Both transcripts can now be read and copied, and the page was reorganised [MAJOR]

**What changed:** A real fault was fixed: when a lecture was transcribed by both the paid service and the free model, only the paid transcript was shown in full, the free model's being replaced by the side-by-side comparison table. The one setting that produces two transcripts was therefore the only setting in which the second could not be read whole or copied. Both now appear in full, each with its own Copy button, and the comparison table still appears underneath; an automated browser check opens such a lecture, presses both buttons and confirms they put different text on the clipboard, so this cannot quietly come back. Three smaller faults were fixed alongside it: the Copy button did nothing visible when the browser refused clipboard access, which looks identical to a broken button, and now says so; clicking a lecture scrolled to the top of the page rather than to the lecture, so it appeared nothing had happened; and "Transcript only" was coloured amber like a warning when it is simply a statement of fact, so warning colours are now reserved for work in progress and red for failures. The list of lectures was also rebuilt as cards rather than a six-column table, which on a phone had to hide half its columns and squeezed the title — the only thing anyone scans by — into a narrow column. The row of stage names became a step indicator with a line joining the steps, and the empty lists now explain what to do instead of saying "Nothing here yet."

**Files touched:**
- `frontend/src/App.jsx` — both transcripts shown with their own Copy buttons, lecture list rebuilt as cards, clicking a lecture scrolls to it, clearer empty messages.
- `frontend/src/App.css` — styling for the cards, the step indicator and the empty messages; warning colours reserved for work in progress.
- `tests/ui_check.py` — opens a both-engine lecture and checks both transcripts appear and each Copy button copies its own one; also saves pictures of the lecture list and of an open lecture.
- `README.md` — a new section explaining how to run the automated checks.

**How to check it:** Open a lecture that was transcribed with "Both" and scroll down: two
complete transcripts appear, each with its own Copy button. Running `bash tests/run_all.sh`
on the server reports "everything passed" (39 service checks and 48 browser checks).

---

## 2026-10-21 — New look: proper fonts and a consistent colour scheme [MINOR]

**What changed:** The website's appearance was rebuilt on a consistent set of design rules instead of the handful of grey shades it had grown by accident: colours, spacing and text sizes are each defined once and referred to by name everywhere else, so light and dark mode can no longer drift apart. Nothing about how the tool works was touched. Two typefaces are now included with the site itself — Inter for the interface and Noto Sans Malayalam for the transcripts — which matters more than it sounds, because Windows computers often have no good Malayalam font installed and transcripts could come out as boxes or badly spaced letters on the very machines the work is reviewed on. They are shipped as part of the website rather than fetched from Google's font service, since the site's security settings deliberately forbid loading anything from outside servers. Transcript text also got a wider gap between lines and a limit on how far a line may run, because Malayalam script is dense and was tiring to read across a wide monitor.

**Files touched:**
- `frontend/src/App.css` — rewritten around a single named set of colours, spacing steps and text sizes, keeping light, dark and "follow the system" behaving exactly as before.
- `frontend/src/main.jsx` — includes the two typefaces in the website.
- `frontend/package.json`, `frontend/package-lock.json` — the two font packages added.
- `tests/ui_check.py` — now checks the fonts genuinely loaded, rather than only that nothing was blocked.

**How to check it:** Open the site and switch between System, Light and Dark at the top right; the page looks the same in shape but cleaner, and the lettering is identical on any computer.

---

## 2026-10-20 — Live progress, and a timer for each transcriber [MAJOR]

**What changed:** Transcribing a 40-minute lecture on our own server takes around five minutes, during which the page previously said only "working". It now shows a progress bar with how many pieces of the lecture are done and roughly how long is left, updating every few seconds. Each job also records how long each transcriber took, shown side by side, so the paid service and the free one can be compared on speed as well as on wording. On a 42-minute lecture the paid service took just under three minutes and the free one just over three, which is a fairer picture than either number alone.

The progress is passed between the two parts of the system through a small file in the shared folder, which avoids reloading the model for every batch just to report a number.

**Also fixed:** the free model kept stopping part-way through long lectures. Its memory use crept upward as it worked, and on a lecture slightly longer than the one used for testing it crossed the ceiling it had been given and was shut down. It now hands memory back after every sixteen pieces, which measurements show keeps its usage flat for the whole lecture — 1,909 MB at the start and 2,071 MB at the end of a 42-minute recording, against a ceiling that is now comfortably above both.

**Files touched:**
- `asr/server.py` — reports progress, works through the lecture in groups and hands back memory between them.
- `backend/main.py`, `backend/local_asr.py`, `backend/db.py` — carry the progress and the two timings through to the website.
- `frontend/src/App.jsx`, `App.css` — the progress bar and the two timers.
- `docker-compose.yml` — more memory headroom for the free model, and writable scratch locations it needs.

**How to check it:** Submit a long lecture with "Local model" selected; the page shows "48 of 107 pieces transcribed (45%) · about 2:23 left" while it works, and both timers once it finishes.

---

## 2026-10-20 — Choose the transcriber, and keep transcripts apart from training data [MAJOR]

**What changed:** Two new choices appear when a lecture is submitted. The first is who transcribes it: the paid ElevenLabs service, which is the most accurate; the free speech recognition model running on our own server, which is weaker but costs nothing; or both, so the two can be compared side by side. This matters because the paid service is nearly two thirds used up, and the free model turns out to be fast enough on our own machine to handle an hour of lecture in about four minutes.

The second choice is what the lecture is for. Until now every lecture was automatically turned into training material, which meant a recording transcribed just to read it would quietly end up in the training set. Now a lecture is either training material or just a transcript, and the list of lectures is split into two accordingly. The export that prepares training data only ever takes from the training list. If a lecture was filed as "just a transcript" and later turns out to be worth training on, a single button converts it, and this costs nothing: the timing information needed to cut it into pieces was already saved, so nothing has to be transcribed again.

The free model cannot be used to make training material, because it does not report when each word was spoken, and those timings are what the cutting depends on. The website explains this rather than silently refusing.

**Files touched:**
- `asr/Dockerfile`, `asr/server.py` — new service holding the free speech recognition model; it loads the model only while a lecture is being transcribed and releases the memory afterwards.
- `backend/local_asr.py` — new; how the main program talks to that service.
- `backend/db.py` — remembers each lecture's chosen transcriber and purpose; older entries keep their old behaviour.
- `backend/main.py` — the different paths through the work, the two lists, the conversion button, and the comparison figures.
- `backend/segmenter.py` — can now split audio by listening for pauses, for the free model which has no word timings.
- `backend/export_dataset.py` — refuses to include anything not marked as training material, and says what it skipped.
- `frontend/src/App.jsx`, `App.css`, `api.js` — the two new choices, the split lists, the conversion button, the side-by-side comparison.
- `docker-compose.yml` — the new service, with its own memory limits.
- `README.md`, `CLAUDE.md` — how the choices work and what each one costs.

**How to check it:** On https://forge.rejected.cc, submit a short video with "Local model" chosen; the credit count on the page should be exactly the same before and after, because nothing was sent to the paid service.

**Fault found on a real lecture (same day):** the free model worked on a short test clip but failed on a genuine 41-minute lecture with a message about not reaching the service. The cause was memory: the model itself occupies nearly 2 GB, and processing eight pieces of audio at once pushed it past the ceiling set for it, so the system shut it down abruptly — which looked to the website like the service had disappeared. Short test clips never showed this because they only ever make one batch. It now processes two pieces at a time and refuses any request to do more, which measurements showed costs only a few percent of speed on an ordinary processor. Re-tested on the full 41-minute lecture: it finished in about five minutes and produced a complete transcript, with nothing spent at the paid service. A related problem was fixed at the same time: the shared memory area containers get by default is far too small for this kind of work, and has been enlarged.

**Tested on the server:** all three choices were run on a short public video. The free model produced a transcript without touching the paid service; choosing both produced two transcripts side by side; and converting a transcript into training material produced the audio pieces and the list file while the credit count stayed exactly the same. The whole round of testing used 21 credits in total, all from the single paid transcription. Two faults were found and fixed along the way: the free model would not start at all because it could not work out which kind of model it was loading, and it then refused to run because, as an ordinary user rather than an administrator, it had nowhere to store its working files.

---

## 2026-10-20 — One settings file, and a round of security hardening [MAJOR]

**What changed:** The project kept its passwords and keys in three separate files, which meant three chances to set the wrong permissions and three things to keep in step. They are now a single file, but each part of the system is still handed only the settings it actually needs: the transcription service never receives the website password, and the web server never receives the transcription key, so a problem in one cannot expose the other's secrets. Alongside that, a review of the running server led to four fixes. The transcription service no longer runs with full administrator rights inside its container, which matters because it feeds downloaded and uploaded files through outside tools. Both services now have memory and processor ceilings, so one bad recording cannot take the whole machine down. The website now sends the standard browser safety instructions it was missing. And a folder of settings and recordings was being handed to the container system on every build of the training environment for no reason; it no longer is.

Also added four gigabytes of swap space, which acts as a cushion if memory ever runs short, and deliberately configured so the two services cannot use it: reaching their memory ceiling means something is wrong, and swapping would only make the whole machine slow instead of failing quickly and restarting.

**Files touched:**
- `docker-compose.yml` — each service now receives only its own settings; the transcription service runs as an ordinary user; memory, processor and swap ceilings added.
- `caddy/Caddyfile` — added the browser safety instructions (secure-connection enforcement, content rules, anti-framing).
- `.dockerignore` (new) — stops settings files and the recordings folder being handed to the container system during builds.
- `.env.example`, `.gitignore`, `README.md`, `CLAUDE.md` — updated for the single settings file.
- `backend/.env.example` — removed; it is part of the single template now.

**How to check it:** Open https://forge.rejected.cc; it works exactly as before. The underlying checks are in the README's Part 2 section.

---

## 2026-10-20 — Server rebuilt from scratch on a larger machine [MAJOR]

**What changed:** The server was wiped and reinstalled, and the website was rebuilt on it from the backup taken beforehand. The replacement machine is considerably better: three times the disk space, twice the memory and an extra processor, which removes the storage problem that had caused a failed build the day before. The website is live again at the same address with a fresh security certificate, and the automatic checks all pass. The collected lecture has deliberately NOT been put back yet, at the owner's request, so the site currently shows an empty list; the data is safe in the backup on the development PC and can be restored at any time.

**Files touched:**
- None in the project. The work was on the server: installing the programs, restoring the three password files from the backup, and starting the website.

**How to check it:** Open https://forge.rejected.cc and log in; the site loads and the job list is empty.

---

## 2026-10-20 — Full backup taken before any server rebuild [MINOR]

**What changed:** Checked whether the project could survive the server being wiped, and found that it could not: the programs were safe on the development PC, but the collected lecture, its transcript, the 192 training clips and the first measurement existed only on the server. Copied all of it to the PC and confirmed, file by file, that the copies are identical to the originals. Also saved a fresh copy of the programs, since the previous saved copy was from before a day of work. Deliberately left the three password files out of the backup; they take minutes to recreate and are safer not duplicated. Written instructions for rebuilding the server from scratch are included with the backup.

**Files touched:**
- `F:\vps-backup-2026-10-20\` (outside the project) — the lecture data, a current copy of the programs, and step-by-step restore instructions.

**How to check it:** Open `F:\vps-backup-2026-10-20\RESTORE.md`.

---

## 2026-10-20 — First real measurement of the Malayalam model [MAJOR]

**What changed:** Ran the whole measuring pipeline for the first time, on the server's ordinary processor, using three clips from the Matrix lecture. It worked from end to end: the model was downloaded, it listened to the clips, and its transcription was scored against the ElevenLabs transcript. The first score is a 70% word error rate and a 48% character error rate. Two faults were found and fixed on the way. The important discovery is that this model, left to itself, already writes English words in Malayalam letters: it wrote "portion" and "start" as പോർഷൻ and സ്റ്റാർട്ട്. That is exactly the approach chosen earlier for the training text, so the plan and the model agree. It also explains why the first score looks so poor: 40% of the words in the reference are still written in English letters, and the model is incapable of producing an English letter, so every one of those counts as a mistake before it has even been judged on its listening. Filling in the English spelling table is expected to improve the score substantially. A pleasant surprise: the measuring step is fast enough on an ordinary processor that no graphics card is needed for it at all.

**Files touched:**
- `gpu/common.py` — the model has to be told which of its twenty-two languages to use, or it stops with an error; it is now told "Malayalam". Also, failures to download the model now explain the real reason instead of a generic message.

**How to check it:** The scores and the clip-by-clip comparison are in `data/results/dryrun/baseline/` on the server.

---

## 2026-10-20 — Two problems fixed while testing the training image [MINOR]

**What changed:** Testing the training environment built earlier the same day turned up two faults, both now fixed. First, the environment would not start at all: AI4Bharat's toolkit asks for a helper library "version 2.2.1 or newer", and the newest version has since removed a component the toolkit still expects, so a fresh install was broken on arrival. The recipe now asks for the exact version the toolkit was written against. Second, the server ran completely out of disk space while rebuilding the environment, because the training environment is about 11 GB and rebuilding it needs room for a second copy. Space was reclaimed, the website and all lecture data were checked and were unharmed, and the fix was applied in a way that does not need a second copy. The server is comfortable for running this environment but is close to its limit for rebuilding it.

**Files touched:**
- `gpu/Dockerfile` — now asks for the exact helper library version, with a comment explaining why.
- `README.md` — notes how much disk space building needs, and how to reclaim space.

**How to check it:** On the server, `docker run --rm lecture-indicconformer:v1 python -c "import nemo.collections.asr"` finishes without an error.

---

## 2026-10-20 — Training machine image and scripts [MAJOR]

**What changed:** Built the self-contained environment that will run on the rented graphics-card machine, so that renting one is a matter of starting it, running a single command, and shutting it down again. It contains AI4Bharat's own version of the NeMo toolkit (the ordinary public version cannot open the Malayalam model file at all) together with five scripts: fetch the lectures from our server, measure the untrained model, train it, measure it again, and send the results back. Deliberately left out the parts of the toolkit meant for large language models, which take hours to compile and are not used here. Building this does not need a graphics card, only disk space, so it is built on our own server; a graphics card is only needed for the training step itself. Nothing was uploaded anywhere, as requested.

**Files touched:**
- `gpu/Dockerfile` — recipe for the training environment.
- `gpu/common.py` — shared code for loading the model, reading the data and calculating the error rates, using the same text clean-up as the website so the comparison is fair.
- `gpu/baseline.py` — measures the untrained model, trying both of its two ways of decoding speech.
- `gpu/finetune.py` — trains the model, and with a small amount of data deliberately trains only the upper part of it, recording that decision and the reason in the run log.
- `gpu/evaluate.py` — measures the trained model in exactly the same way, so the two can be compared.
- `gpu/pull_data.sh`, `gpu/push_results.sh`, `gpu/run_all.sh` — move the data in, the results out, and run everything in order, stopping at the first problem.
- `README.md` — new section covering building the image, the settings it needs, a separate restricted login for the rented machine and how to remove it afterwards, and what a run produces.

**How to check it:** On the server, `docker images` lists `lecture-indicconformer`. The training scripts cannot be fully tested without a graphics card; the next step tests the measuring part on the server's ordinary processor.

---

## 2026-10-20 — Dataset export built; two test lectures deleted [MAJOR]

**What changed:** Built the script that turns the collected lectures into a training dataset: it gathers the short clips and their text, cleans the text, and splits the lectures into three groups (training, checking, testing). Lectures are always kept whole, never split into pieces across groups, because testing a model on a lecture it has already studied gives a flattering but meaningless score. Also deleted two lectures at the owner's request: the Physics test lecture and the short upload test cut from it. That leaves one lecture, so the export correctly refuses to call the result usable and explains that at least three lectures are needed. While testing, found and fixed a fault in our own code that was quietly deleting English words from the training text instead of keeping them; four clips had been thrown away as "empty" because of it, and all 192 are now kept.

**Files touched:**
- `backend/export_dataset.py` — new script that builds the dataset folder, with a report of what was included, dropped, and still missing.
- `backend/build_english_map.py` — new script that lists every English word used and builds the fixed spelling table.
- `backend/normalise.py` — fixed the fault that deleted English words that had no spelling yet; they are now kept and reported.
- `backend/test_normalise.py` — three more checks so that fault cannot come back; 26 checks in total.
- `README.md` — new "Part 2 — Model training" section explaining the plan, the commands, the dataset folder, and what the scores do and do not mean.

**How to check it:** On the server, run `docker compose exec backend python export_dataset.py --dry-run`; it prints what each group would contain and why the data is not yet enough.

---

## 2026-10-20 — Decided how English words will be handled, and built the text clean-up module [MAJOR]

**What changed:** Checked what the Malayalam speech model is actually capable of writing, by reading its vocabulary list (the complete set of symbols it is able to produce). It has only 256 symbols, all Malayalam, and not a single English letter or ordinary digit. The model therefore cannot write "matrix" at all, no matter how much it is trained, unless its output layer is rebuilt. Since about two words in every five in our lectures are English, and we will only have a few hours of recordings, rebuilding the output layer is not realistic. The decision is therefore to write English words in Malayalam letters in the training text, using one fixed spelling per word so the same word is never spelled two ways. Also built the text clean-up module that both the training and the scoring will use, because Malayalam has letters that can be written two different ways while looking identical on screen; without this clean-up, correct recognition would be counted as a mistake.

**Files touched:**
- `backend/normalise.py` — new shared module that cleans up text the same way everywhere: fixes the two ways of writing chillu letters, removes invisible characters and punctuation, and rewrites English words in Malayalam letters using a fixed table.
- `backend/test_normalise.py` — 23 automatic checks for that module, including real examples taken from our own transcripts.

**How to check it:** On the server, run `docker compose exec backend python -m unittest test_normalise -v`; all 23 checks should say "ok".

---

## 2026-10-20 — Part 2 begins: measuring how much English is in the lectures [MAJOR]

**What changed:** Started Part 2, the stage where the collected lectures are used to train the speech recognition model. Before any training code can be written, we need to know how the lecturers actually speak: the recorded lectures mix Malayalam and English, and the Malayalam model can only write in Malayalam letters, so a decision is needed about what to do with the English words. Wrote a report script that counts, across every finished lecture, how many words are in Malayalam script, how many in English, which English words come up most, and how much of each lecture is affected. The result: about 2 words in every 5 are English, and almost every short clip contains at least one English word.

**Files touched:**
- `backend/analyze_script.py` — new report script that reads the stored transcripts and prints the Malayalam/English breakdown; it only reads and changes nothing.

**How to check it:** On the server, run `docker compose exec backend python analyze_script.py` inside the project folder; the report prints in the terminal.

---

## 2026-10-20 — ElevenLabs credits shown on the website [MAJOR]

**What changed:** Added a card at the top of the website showing how many ElevenLabs credits (the prepaid units the transcription service charges) are left on the account, how many are used, and when they reset. ElevenLabs only gives out these numbers if the API key has the extra read-only permission "User: Read"; until that is switched on, the card says exactly which permission is missing. The card also shows how much audio this tool has transcribed in total, counted from its own job list, which works without any extra permission. ElevenLabs does not allow a key to read its own per-key spending limit, so that limit can only be seen in the ElevenLabs dashboard.

**Files touched:**
- `backend/transcriber.py` — new function that asks ElevenLabs for the account's credit usage.
- `backend/db.py` — new function that totals the finished jobs and minutes of audio.
- `backend/main.py` — new web address that returns both, asking ElevenLabs at most once a minute.
- `frontend/src/App.jsx` — new credits card with a usage bar (turns red above 90 %).
- `frontend/src/App.css` — styles for the credits card.
- `frontend/src/api.js` — added the address of the usage information.
- `README.md` — documented the new address and which API key permissions are needed.

**How to check it:** Open https://forge.rejected.cc; the credits card is under the title (after enabling "User: Read" on the key it shows the numbers).

---

## 2026-10-19 — Fix: update script stopped half-way [MINOR]

**What changed:** The script that copies updates to the server stopped with an error while the server was rebuilding, because Windows PowerShell mistook Docker's normal progress messages for errors. The script now only stops on real failures. The server was not affected: the old version kept running until the update was re-run successfully.

**Files touched:**
- `deploy.ps1` — no longer treats Docker's progress messages as errors.

**How to check it:** Run the update script; it finishes with the list of running containers.

---

## 2026-10-19 — Audio download, dark theme and YouTube links [MAJOR]

**What changed:** Added three things to the website. Each job now has a "Download audio" button that saves the extracted lecture audio (the same 16 kHz single-channel WAV file used for training), named after the video title. A System / Light / Dark switch in the top corner changes the colours; "System" follows the computer's own setting, and the choice is remembered in that browser. The original YouTube link of each transcribed video is shown on the job page and in the list of previous jobs, and opens the video in a new tab.

**Files touched:**
- `backend/main.py` — new web address for downloading a job's audio, and each job now reports whether its audio file is ready.
- `frontend/src/App.jsx` — added the theme switch, the YouTube link and the download button.
- `frontend/src/theme.js` — new file that remembers and applies the chosen colour theme.
- `frontend/src/main.jsx` — applies the saved theme as soon as the page opens, so it doesn't flash white first.
- `frontend/src/api.js` — added the address of the audio download.
- `frontend/src/App.css` — dark colour set, and styles for the switch, link and button.
- `README.md` — documented the new download address and the website features.

**How to check it:** Open https://forge.rejected.cc, click a finished YouTube job: the link and "Download audio" button appear under the title; use the switch at the top right to change the theme.

---

## 2026-10-19 — Version 0.1 snapshot [MINOR]

**What changed:** Saved the whole project as version 0.1 in a single zip file, as a record of the first working version (live online and tested with a real lecture). Secret settings files (the ElevenLabs key and the website password) and the test data folder are deliberately left out of the zip.

**Files touched:**
- `PROJECT_DIARY.md` — this entry.

**How to check it:** Open `lecture-transcriber-v0.1.zip` next to the project folder on the development PC.

---

## 2026-10-19 — Tool is live at forge.rejected.cc [MAJOR]

**What changed:** Moved the tool onto the project server (VPS) and started it there, so it is now available online at https://forge.rejected.cc behind a username and password. Set up a secure key-based login from the development PC to the server so updates can be copied over automatically, and placed the project in the server's standard folder for Docker projects (/opt/docker). Tested the full pipeline with a real 8-minute Malayalam physics lecture from YouTube: it was transcribed in under 2 minutes, the language was detected as Malayalam, English technical words such as "scalar" and "vector" were kept in English, and it produced 33 training clips of 6–20 seconds with a matching manifest. An uploaded audio file was also processed successfully.

**Files touched:**
- `deploy.ps1` — now sends the project to the new server and folder, keeps the folder's permissions, and leaves out editor settings.
- `README.md` — added the one-time key login setup, the /opt/docker location, the server's SSH port in the firewall rules, and updated all server paths.
- `CLAUDE.md` — recorded the server address, login alias and project folder.

**How to check it:** Open https://forge.rejected.cc, log in, and open the job "Physics 11 Chapter 3 | Scalars And Vectors | Malayalam" in the list of previous jobs.

---

## 2026-10-19 — Running the whole tool on the development PC [MINOR]

**What changed:** Started the complete tool (website, web server and backend) on the development PC, exactly as it will run on the server, so it can be tried out at http://localhost before going online. Tested it end to end: the login is required, a YouTube video and an uploaded video were both downloaded and converted correctly, and both jobs stopped at the transcription step with a clear message because the ElevenLabs key has not been added yet. Added a short "local test" section to the README.

**Files touched:**
- `README.md` — new section explaining how to run the tool locally on the Windows PC with Docker Desktop.

**How to check it:** With Docker Desktop running, open http://localhost and log in.

---

## 2026-10-19 — Website, secure web server and documentation [MAJOR]

**What changed:** Built the website people use to submit lectures: it has a "YouTube link" tab and an "Upload file" tab, an optional box for technical key terms, a live progress display that refreshes every 3 seconds, the finished transcript with a fold-out table of the short clips, and a list of earlier jobs. Set up Caddy, a web server that automatically gets an HTTPS certificate (the padlock in the browser), asks for a username and password before showing anything, and passes requests to the backend. Also wrote the README (setup and troubleshooting guide) and a small script that copies the project from the development PC to the server.

**Files touched:**
- `frontend/` (all files) — the React website: page layout, styling, and the code that talks to the backend.
- `frontend/Dockerfile` — builds the website and places it inside the Caddy web server's container.
- `caddy/Caddyfile` — web server settings: HTTPS, password protection, 2 GB upload limit, forwarding of /api requests.
- `docker-compose.yml` — added the Caddy web server next to the backend.
- `.env.example` — template for the domain name and website login.
- `deploy.ps1` — copies the project to the server with scp (secure copy) and restarts it there; passwords and data are never copied.
- `README.md` — full setup, daily commands, data format and troubleshooting guide.

**How to check it:** Tested on the development PC: the site loads, wrong or missing passwords are refused, and uploads go through the web server. The final test at https://forge.rejected.cc is still pending (server access needed).

---

## 2026-10-19 — Processing pipeline: download, transcribe, cut into training clips [MAJOR]

**What changed:** Built the backend, the part that does the actual work. For a YouTube link it downloads only the audio using yt-dlp (a YouTube download tool); for an uploaded audio or video file it takes the file directly. FFmpeg (an audio conversion tool) converts the audio to the format speech models are trained on (16 kHz, single channel). The audio is sent to ElevenLabs Scribe v2 (a paid speech-to-text service) with word-level timing switched on. The transcript is then split into short clips of about 5–20 seconds, always cut at the longest pause between words, and a "manifest" file is written listing each clip and its text, ready for training the IndicConformer model in Part 2.

**Files touched:**
- `backend/main.py` — the web API (the addresses the website calls) and the step-by-step processing of each job.
- `backend/downloader.py` — YouTube download, link checking, and conversion with FFmpeg.
- `backend/transcriber.py` — sends audio to ElevenLabs and turns its errors (wrong key, no credits) into clear messages.
- `backend/segmenter.py` — splits the lecture into short clips and writes the training manifest.
- `backend/db.py` — keeps the list of jobs and their status in a small SQLite database file.
- `backend/requirements.txt` — the Python libraries the backend needs.
- `backend/Dockerfile` — recipe for the backend container, including FFmpeg.
- `backend/.env.example` — template for the secret ElevenLabs key (the real key is never stored with the code).

**How to check it:** Tested on the development PC: a YouTube video downloaded and converted correctly, uploads of video files and broken files behaved correctly, a wrong API key gave a clear error, and the clip-cutting passed an automated check (no words lost, no clip over 20 seconds). A real transcription test needs the ElevenLabs key on the server.

---

## 2026-10-19 — Initial project setup [MAJOR]

**What changed:** Created the project structure for Part 1 of the capstone: a web tool that collects lecture audio and transcripts for training a Malayalam-English speech recognition model later (Part 2). Checked the development PC and confirmed that Docker (software that runs each part of the system in its own isolated "container") works and that YouTube downloads are possible. Wrote the project rules file so future work follows the same conventions.

**Files touched:**
- `CLAUDE.md` — the project's working rules: purpose, where it runs, fixed technology choices, and the requirement to keep this diary.
- `PROJECT_DIARY.md` — this diary, started with this entry.
- `.gitignore` — lists files that must never be shared, such as secret keys and the large data folder.
- `.env.example` and `backend/.env.example` — templates showing which settings are needed, without the secret values.

**How to check it:** Open the project folder and confirm these files exist.
