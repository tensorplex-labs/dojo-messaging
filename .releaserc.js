
const currentBranch = process.env.GITHUB_REF_NAME || process.env.BRANCH_NAME || '';
const isPrereleaseBranch = ['staging', 'prerelease', 'develop', 'dev'].includes(currentBranch);

module.exports = {
  branches: ["main",
    {
      name: 'staging',
      prerelease: 'rc',        // Release candidates: 1.0.0-rc.1, 1.0.0-rc.2
    },
    {
      name: 'develop',
      prerelease: 'beta',      // Beta releases: 1.0.0-beta.1
    }

  ],
  plugins: [
    [
      "@semantic-release/commit-analyzer",
      {
        preset: "conventionalcommits",
        releaseRules: [
          { type: "feat", release: "minor" },
          { type: "fix", release: "patch" },
          { type: "perf", release: "patch" },
          { type: "revert", release: "patch" },
          { type: "docs", release: "patch" },
          { type: "style", release: "patch" },
          { type: "refactor", release: "patch" },
          { type: "test", release: "patch" },
          { type: "build", release: "patch" },
          { type: "ci", release: "patch" },
          { type: "chore", release: "patch" },
        ],
      },
    ],
    [
      "@semantic-release/release-notes-generator",
      {
        preset: "conventionalcommits",
        presetConfig: {
          types: [
            { type: "feat", section: "✨ Features" },
            { type: "fix", section: "🐛 Bug Fixes" },
            { type: "perf", section: "🥷 Performance Improvements" },
            { type: "revert", section: "⏪ Reverts" },
            { type: "docs", section: "🔧 Misc" },
            { type: "style", section: "🔧 Misc" },
            { type: "refactor", section: "📦 Code Refactoring" },
            { type: "test", section: "🔧 Misc" },
            { type: "build", section: "🔧 Misc" },
            { type: "ci", section: "🔧 Misc" },
            { type: "chore", section: "🔧 Misc" },
          ],
        },
        writerOpts: {
          headerPartial: "## 🚀 v{{version}} ({{date}})\n\n",
// Enhanced transform function for your releaserc.js
        transform: (_commit, context) => {
          const commit = { ..._commit };

          // Clean repository URL by removing /blob and version parts
          const baseRepoUrl = context.repository.replace(/\/blob\/.*$/, '');

          // Add PR link if available
          if (commit.pullRequest && commit.pullRequest.number) {
            commit.pr = ` ([#${commit.pullRequest.number}](${baseRepoUrl}/pull/${commit.pullRequest.number}))`;
          } else {
            commit.pr = "";
          }

          // Add issue references if found in commit message
          const issueRegex = /#(\d+)/g;
          const issues = [];
          let match;
          while ((match = issueRegex.exec(commit.subject || "")) !== null) {
            issues.push(`[#${match[1]}](${baseRepoUrl}/issues/${match[1]})`);
          }
          commit.issues = issues.length > 0 ? ` (${issues.join(", ")})` : "";

          if (commit.hash) {
            commit.shortHash = ` ([\`${commit.hash.substring(0, 7)}\`](${baseRepoUrl}/commit/${commit.hash}))`;
          } else {
            commit.shortHash = "";
          }

          // Parse co-authors from commit body/footer
          function parseCoAuthors(commitBody, commitFooter) {
            const coAuthors = [];
            const coAuthorRegex = /Co-authored-by:\s*([^<\n]+)(?:\s*<([^>\n]+)>)?/gi;

            // Check both body and footer for co-authors
            const textToSearch = [commitBody, commitFooter].filter(Boolean).join('\n');

            let match;
            while ((match = coAuthorRegex.exec(textToSearch)) !== null) {
              const name = match[1].trim();
              const email = match[2] ? match[2].trim() : null;
              coAuthors.push({ name, email });
            }

            return coAuthors;
          }

          // Get co-authors
          const coAuthors = parseCoAuthors(commit.body, commit.footer);

          // Build author information
          const mainAuthor = commit.author && commit.author.name ? commit.author.name : '';

          if (coAuthors.length > 0) {
            const coAuthorNames = coAuthors.map(author => author.name);
            commit.authorName = ` @${mainAuthor} & @${coAuthorNames.join(', @')}`;
          } else {
            commit.authorName = ` @${mainAuthor}`;
          }

          // Type transformations (your existing code)
          if (commit.type === "feat") {
            commit.type = "✨ Features";
          } else if (commit.type === "fix") {
            commit.type = "🐛 Bug Fixes";
          } else if (commit.type == 'perf'){
            commit.type = "🥷 Performance Improvements";
          } else if (commit.type === 'refactor') {
            commit.type = "📦 Code Refactoring";
          } else if (commit.type === 'revert') {
            commit.type = "⏪ Reverts";
          } else if (['docs', 'style', 'test', 'build', 'ci', 'chore'].includes(commit.type)) {
            commit.type = "🔧 Misc";
          }

          return commit;
        },
          commitPartial: "- **{{subject}}**{{shortHash}}{{pr}}{{issues}}{{authorName}}\n",
        },
      },
    ],
    // Only include changelog plugin for main branch (skip for prerelease branches)
    ...(isPrereleaseBranch ? [] : [
      [
        "@semantic-release/changelog",
        {
          changelogFile: "CHANGELOG.md",
        },
      ]
    ]),
    "@semantic-release/github",
    [
      "@semantic-release/git",
      {
        assets: isPrereleaseBranch ? ["package.json"] : ["CHANGELOG.md", "package.json"],
        message: "chore(release): ${nextRelease.version} [skip ci]\n\n${nextRelease.notes}",
      },
    ],
  ],
};
