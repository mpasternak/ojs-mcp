<?php
/**
 * Assign the two demo reviewers to the submission that sits in external
 * review, so the review endpoints have something to return.
 *
 * OJS has no REST endpoint for creating a review assignment -- in the real
 * application an editor does it through the workflow UI -- so this goes
 * through OJS's own repositories instead of the API.
 *
 * Run inside the OJS container:
 *   php /var/www/html/tools/../demo-seed/assign-reviewers.php
 */

require '/var/www/html/tools/bootstrap.php';

use APP\facades\Repo;
use PKP\db\DAORegistry;
use PKP\submission\reviewAssignment\ReviewAssignment;
use PKP\submission\reviewRound\ReviewRound;
use PKP\security\Role;
use PKP\stageAssignment\StageAssignment;

const SUBMISSION_ID = 7;

$submission = Repo::submission()->get(SUBMISSION_ID);
if (!$submission) {
    exit("Submission " . SUBMISSION_ID . " not found\n");
}

$reviewRoundDao = DAORegistry::getDAO('ReviewRoundDAO');
$round = $reviewRoundDao->getLastReviewRoundBySubmissionId(
    SUBMISSION_ID, WORKFLOW_STAGE_ID_EXTERNAL_REVIEW
);
if (!$round) {
    exit("No external review round on submission " . SUBMISSION_ID . "\n");
}

// reviewer1 has returned a recommendation; reviewer2 has been invited and has
// not answered yet. Two states, so the API shows more than one shape.
$plan = [
    ['username' => 'reviewer1', 'responded' => true],
    ['username' => 'reviewer2', 'responded' => false],
];

foreach ($plan as $i => $row) {
    $reviewer = Repo::user()->getByUsername($row['username']);
    if (!$reviewer) {
        echo "no such user: {$row['username']}\n";
        continue;
    }

    $existing = Repo::reviewAssignment()->getCollector()
        ->filterBySubmissionIds([SUBMISSION_ID])
        ->filterByReviewerIds([$reviewer->getId()])
        ->getMany();
    if ($existing->count()) {
        echo "already assigned: {$row['username']}\n";
        continue;
    }

    $assignment = Repo::reviewAssignment()->newDataObject([
        'submissionId' => SUBMISSION_ID,
        'reviewerId' => $reviewer->getId(),
        'reviewRoundId' => $round->getId(),
        'stageId' => WORKFLOW_STAGE_ID_EXTERNAL_REVIEW,
        'round' => $round->getRound(),
        'reviewMethod' => ReviewAssignment::SUBMISSION_REVIEW_METHOD_ANONYMOUS,
        'dateAssigned' => '2026-01-25 10:00:00',
        'dateDue' => '2026-03-01 00:00:00',
        'dateResponseDue' => '2026-02-08 00:00:00',
    ]);

    if ($row['responded']) {
        $assignment->setDateConfirmed('2026-01-27 08:30:00');
        $assignment->setDateCompleted('2026-02-19 16:45:00');
        $assignment->setRecommendation(ReviewAssignment::SUBMISSION_REVIEWER_RECOMMENDATION_PENDING_REVISIONS);
    }

    $id = Repo::reviewAssignment()->add($assignment);
    echo "assigned {$row['username']} (review assignment {$id})\n";
}

echo "done\n";
