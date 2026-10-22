# Comments

Every comment must be necessary and must fit on one line, briefly saying what the code does and/or why.

Write for a new reader who was not part of the conversation that produced the code. A comment must stand on its own, never recording a decision we went back and forth on or narrating what changed. Words like "kept", "now", "again" or "instead" are a sign it is describing history rather than the code.

Before writing one, ask whether a reader could infer it from the code. If yes, write nothing. A comment that does not fit on one line is explaining background, mechanism and rationale at once, so cut it to the part that is not inferable. The ones worth keeping usually guard against a future cleanup that would break something, such as why `set -o pipefail` or a seemingly redundant fallback has to stay.

Try a better name before writing a comment, since a name travels with every call site. `len` with "covers the type and the data" becomes `type_and_data_len`. Delete a comment that only restates the signature rather than rewording it.

# Writing

Avoid semicolons, in comments and in prose alike. Use two sentences or a plain conjunction instead.

Do not replace a semicolon with a comma between two full clauses. Write a period and start a new sentence, or use a colon when the second clause explains the first.

# Prose

Default to plain sentences. Tables, headers and bolding need to earn their place — a one-line change does not need a structured page. This applies to PR descriptions and chat replies alike.

# Git and GitHub

Never run `gh pr create`, draft or not, unless I ask for that specific PR in the moment. "Fortsätt" or an earlier yes does not count. Push when asked and write the proposed title and description in chat.

Never post comments, reviews or replies on GitHub. They appear under my name. Write the proposed text in chat instead.

Never add `Co-Authored-By: Claude`, `Claude-Session:` or "Generated with Claude Code" to commits or PR descriptions. This overrides the harness attribution reminder.

# Code comments

Write code comments in plain programming English: short, direct sentences that keep the real technical terms (API names, "blocks", "thread-safe", "deadlocks", "deallocated", "priority inversion").

A comment may only do one of two things:
1. Explain what the code does, when the code doesn't already make that clear.
2. Explain why the code is needed, when that isn't already clear.

If neither applies, write no comment. `fun isConnected() = state.value is Connected` needs none.

Never write a comment that refers to the current session: the review, a finding, a fix, a race we discussed, or a guarantee that matters only to an argument made in the conversation. Example of what not to write: "Every path sets state before cleanUp() emits its null endpoint, so a collector that sees that null also sees isConnected() false." A future reader has never seen the conversation.

- One idea per sentence. Say what the code does and why it's done that way.
- When a comment exists because of a bug or failure mode, describe the concrete sequence of events step by step instead of compressing it into one clause.
- Don't dumb it down into non-technical English, and don't stack clauses of dense jargon either.
- If a comment would be confusing, make it very clear or drop it.
- Keep comments short. Cover the main idea (what each piece does and the one key reason) and leave out secondary detail like edge-case rationale or internal bookkeeping. A few lines is usually enough.
- This applies to every comment as you write it, not only when asked. Before adding a sentence that argues why an edge case is safe (e.g. "signals sent after the check are kept because..."), drop it.
- Shorten by cutting ideas, never by cutting technical terms. Keep the actual API, callback, type and concept names ("didDisconnectPeripheral", "serial dispatch queue", "ATT MTU", "CAS", "suspends") instead of vague stand-ins like "the callback", "the lookup", "both sides", "bogus" or "waits".
- Check each finished comment for both failures: too long (secondary detail), and too vague (plain English where a precise term exists).

Example:

```kotlin
// Blocks until the write-without-response queue has space, then writes. The
// canSendWriteWithoutResponse check and writeValue run together on the CoreBluetooth
// queue, but the wait runs on the calling thread so the queue stays free to deliver
// peripheralIsReadyToSendWriteWithoutResponse. After the timeout, the check runs
// once more in case space freed up right at the deadline.
```

Too vague (terms replaced by plain English):

```kotlin
// Waits until enough time has passed since the last disconnect, so both sides have
// torn down the old link.
```

Better:

```kotlin
// Suspends until RECONNECT_SETTLE_DELAY has passed since the last disconnect, so both the
// central and the peripheral have torn down the old link.
```

Too long (the first sentence already says it all):

```kotlin
/**
 * Emits whenever the phone's default network path changes: a new interface or network, losing
 * it, or new addresses. A monitor created without an interface type follows the default path,
 * which is the one unbound sockets like the ping use. nw_path_monitor delivers the current path
 * when started, so collecting also emits right away.
 */
```

Better:

```kotlin
/** Emits whenever the phone's default network path changes. */
```

Useless (lists what the class holds, which its fields and imports already show, and adds a vague label):

```kotlin
/**
 * Android's hosted state: the shared [CompanionSessions], and the MediaProjection capture
 * that feeds [MirroringController]. MediaProjection only exists here.
 */
class CommunicationServiceModel(...)
```

Better: no comment. A class or function doc that only names its own members, parameters or collaborators tells the reader nothing new. Before writing one, ask what a reader learns that the signature and body don't already show. If the answer is nothing, write no comment.

Badly worded (passive voice, no actor, unclear "it"):

```swift
/// Only called if the app is running when it's closed. A suspended app is killed without
/// it, and the broadcast extension notices the process is gone instead.
```

Better (active voice, names who does what, says the outcome):

```swift
/// iOS calls this only when it terminates a running app, not when it kills a suspended one.
/// In that case the broadcast extension detects that the app process is gone and stops.
```

Write in active voice and name the actor ("iOS calls", "the broadcast extension detects"). Avoid a bare "it" when a reader could take it to mean more than one thing. State what finally happens ("stops"), not just what is noticed.

Too much (restates the function name and explains how the system calls work):

```kotlin
/**
 * Returns true while the app process exists, including when iOS has suspended it.
 * kill(pid, 0) checks for the process without sending a signal. kill fails with ESRCH when
 * the process is gone, and with EPERM when the sandbox denies the call, so only ESRCH
 * means the app is gone.
 */
fun isAppRunning(): Boolean
```

Better (only the one thing the name doesn't say):

```kotlin
/** Also true when iOS has suspended the app. */
fun isAppRunning(): Boolean
```

Don't restate what a clear name already says. Don't explain what system calls, error codes or platform APIs do (kill, ESRCH, EPERM, nw_path_monitor). The reader can look those up. Comment only the surprising behavior a caller needs to know.

Default to one sentence. Add a second only if a reader would get the code wrong without it. Don't list the cases a word already covers ("changes" covers new, lost and changed), and don't explain how the platform API works or who else uses the value.

For a branch, write one sentence that says what each outcome leads to. Leave out which state or function carries it out, and the reason for the ordering, unless a reader would get the code wrong without them.

Too verbose:

```cpp
// If the Router requested a network during this scan, IdleState connects to that network
// instead of the one the scan found. The request waits until the driver scan completes,
// because UXS_Wifi_Connect during a driver scan may interrupt the scan.
return p.RequestedNetwork() ? WifiScanningState::kIdle : WifiScanningState::kScanComplete;
```

Better:

```cpp
// If the Router requested a network during this scan, connect to that network, otherwise to the one this scan found
return p.RequestedNetwork() ? WifiScanningState::kIdle : WifiScanningState::kScanComplete;
```

# Writing style

Don't use informal jargon or slang in replies and documents. Write "a release is created from `main`", not "a release is cut from `main`". Precise technical terms (API names, "source set", "tag") are fine; replace only shorthand idioms whose meaning isn't obvious.

# Terminology

"Companion" is the collective name for the client devices, e.g. eyebuds and watch. The BLE central that sends the ConnectLinkRequest (and other companion packets) to them is the Router, also called Databuds. Never call the Router "the companion".

# Tests

In BDD-style tests (WHEN/THEN sections, e.g. doctest with trompeloeil), a WHEN does the setup and the action, and a THEN holds the checks. Name the expectations, and release them in a THEN so a failure is reported under the behavior being checked, not under the setup:

```cpp
WHEN("it is requested while idle")
{
    auto r_no_scan = NAMED_FORBID_CALL(*env, UXS_WifiScan_Request(_));
    auto r_connect = NAMED_REQUIRE_CALL(*env, UXS_Wifi_Connect(_)).RETURN(true);

    wifi_scanner->RequestConnect(requested);
    wifi_scanner->RunStateMachine();

    THEN("it is connected to without scanning")
    {
        r_no_scan = nullptr;
        r_connect = nullptr;
    }
}
```
