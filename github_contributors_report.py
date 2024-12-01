import os
import sys
import argparse
import logging
import json
from datetime import datetime, timedelta, timezone

import pandas as pd
from github import Github, GithubException

import plotly.express as px
from dash import Dash, dcc, html, dash_table, Input, Output, State, Patch, clientside_callback
import dash_bootstrap_components as dbc
from dash_bootstrap_templates import load_figure_template
import plotly.io as pio

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Initialize GitHub client
GH_TOKEN = os.getenv('GH_TOKEN')
ORGS = os.getenv('GITHUB_ORGS')

if not GH_TOKEN:
    logging.error("Please set the GH_TOKEN environment variable.")
    sys.exit(1)

if not ORGS:
    logging.error("Please set the GITHUB_ORGS environment variable.")
    sys.exit(1)

ORGS = ORGS.split(',')

g = Github(GH_TOKEN, per_page=100)

def get_repos(org_names, exclude_repos=None, top_n_repos=None):
    repos = []
    for org_name in org_names:
        try:
            org = g.get_organization(org_name.strip())
            logging.info(f"Fetching repositories for organization: {org_name.strip()}")
            for repo in org.get_repos(type='all'):
                if not repo.archived:
                    repos.append(repo)
            logging.info(f"Fetched {len(repos)} unarchived repositories for '{org_name.strip()}'.")
        except GithubException as e:
            logging.error(f"GitHub API error for organization '{org_name.strip()}': {e}")
        except Exception as e:
            logging.error(f"Error fetching repositories for '{org_name.strip()}': {e}")
    logging.info(f"Total repositories fetched: {len(repos)}")

    # Apply exclude_repos filter
    if exclude_repos:
        exclude_list = [s.strip() for s in exclude_repos.split(',')]
        logging.info(f"Excluding repositories containing any of: {exclude_list}")
        repos = [repo for repo in repos if not any(sub in repo.full_name for sub in exclude_list)]
        logging.info(f"Repositories after exclusion: {len(repos)}")
    else:
        logging.info(f"No repositories excluded.")

    if top_n_repos:
        repos_sorted = sorted(repos, key=lambda r: r.pushed_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        top_n = min(top_n_repos, len(repos_sorted))
        repos = repos_sorted[:top_n]
        logging.info(f"Selected top {top_n} repositories based on most recent update.")
    else:
        logging.info(f"No top-n-repos specified; using all repositories.")

    return repos

def save_raw_data(repos, filename):
    raw_data = []
    for repo in repos:
        raw_data.append({
            'name': repo.name,
            'full_name': repo.full_name,
            'html_url': repo.html_url,
            'private': repo.private,
            'archived': repo.archived,
            'created_at': repo.created_at.isoformat(),
            'updated_at': repo.updated_at.isoformat(),
            'pushed_at': repo.pushed_at.isoformat() if repo.pushed_at else None,
            'language': repo.language,
            'fork': repo.fork,
            'stargazers_count': repo.stargazers_count,
            'watchers_count': repo.watchers_count,
            'forks_count': repo.forks_count,
            'open_issues_count': repo.open_issues_count
        })
    df = pd.DataFrame(raw_data)
    df.to_csv(filename, index=False)
    logging.info(f"Raw repository data saved to '{filename}'.")

def fetch_contributions(repo_full_name, fetch_slow_data=False, since=None, until=None):
    contributions = {
        'commits': [],
        'prs': [],
        'comments': []
    }
    try:
        repo = g.get_repo(repo_full_name)
        logging.info(f"Fetching commit stats for repository: {repo_full_name}")
        contributors_stats = repo.get_stats_contributors()
        if contributors_stats:
            for contributor in contributors_stats:
                author = contributor.author.login if contributor.author else 'Unknown'
                total_commits = contributor.total
                total_additions = sum(week.a for week in contributor.weeks)
                contributions['commits'].append({
                    'author': author,
                    'total_commits': total_commits,
                    'total_additions': total_additions
                })
        else:
            logging.warning(f"No contributors stats found for repository '{repo_full_name}'.")

        if fetch_slow_data:
            logging.info(f"Fetching PRs for repository: {repo_full_name}")
            pulls = repo.get_pulls(state='all', sort='created', direction='desc')
            for pr in pulls:
                pr_created_at = pr.created_at.replace(tzinfo=timezone.utc)
                if since and pr_created_at < since:
                    break
                contributions['prs'].append({
                    'pr_id': pr.id,
                    'number': pr.number,
                    'title': pr.title,
                    'user': pr.user.login if pr.user else 'Unknown',
                    'state': pr.state,
                    'created_at': pr.created_at.isoformat(),
                    'merged_at': pr.merged_at.isoformat() if pr.merged_at else None,
                    'additions': pr.additions,
                    'deletions': pr.deletions,
                    'changed_files': pr.changed_files
                })
            logging.info(f"Fetched {len(contributions['prs'])} PRs for '{repo_full_name}'.")

            logging.info(f"Fetching Comments for repository: {repo_full_name}")
            for pr in pulls:
                if since and pr.created_at.replace(tzinfo=timezone.utc) < since:
                    continue
                for comment in pr.get_comments():
                    comment_created_at = comment.created_at.replace(tzinfo=timezone.utc)
                    if since and comment_created_at < since:
                        continue
                    contributions['comments'].append({
                        'comment_id': comment.id,
                        'pr_number': pr.number,
                        'user': comment.user.login if comment.user else 'Unknown',
                        'body': comment.body,
                        'created_at': comment.created_at.isoformat()
                    })
            logging.info(f"Fetched {len(contributions['comments'])} comments for '{repo_full_name}'.")
    except GithubException as e:
        logging.error(f"GitHub API error for repository '{repo_full_name}': {e}")
    except Exception as e:
        logging.exception(f"Error fetching contributions for '{repo_full_name}': {e}")
    return contributions

def save_contributions_data(contributions, filename):
    with open(filename, 'w') as f:
        json.dump(contributions, f, indent=4)
    logging.info(f"Contributions data saved to '{filename}'.")

def load_contributions_data(filename):
    if not os.path.exists(filename):
        logging.error(f"Contributions file '{filename}' does not exist.")
        sys.exit(1)
    with open(filename, 'r') as f:
        contributions = json.load(f)
    logging.info(f"Loaded contributions data from '{filename}'.")
    return contributions

def parse_and_aggregate(contributions, since=None, until=None, fetch_slow_data=False):
    contributor_stats = {}
    repo_stats = {}

    for repo, contribs in contributions.items():
        for commit_data in contribs.get('commits', []):
            author = commit_data['author']
            if author not in contributor_stats:
                contributor_stats[author] = {
                    'pr_count': 0,
                    'commit_count': 0,
                    'comment_count': 0,
                    'lines_added': 0
                }
            contributor_stats[author]['commit_count'] += commit_data['total_commits']
            contributor_stats[author]['lines_added'] += commit_data['total_additions']

            if repo not in repo_stats:
                repo_stats[repo] = {
                    'pr_count': 0,
                    'commit_count': 0,
                    'comment_count': 0,
                    'lines_added': 0
                }
            repo_stats[repo]['commit_count'] += commit_data['total_commits']
            repo_stats[repo]['lines_added'] += commit_data['total_additions']

        if fetch_slow_data:
            for pr in contribs.get('prs', []):
                pr_created_at = datetime.fromisoformat(pr['created_at']).replace(tzinfo=timezone.utc)
                if since and pr_created_at < since:
                    continue
                user = pr['user']
                if user not in contributor_stats:
                    contributor_stats[user] = {
                        'pr_count': 0,
                        'commit_count': 0,
                        'comment_count': 0,
                        'lines_added': 0
                    }
                contributor_stats[user]['pr_count'] += 1
                contributor_stats[user]['lines_added'] += pr.get('additions', 0)

                if repo not in repo_stats:
                    repo_stats[repo] = {
                        'pr_count': 0,
                        'commit_count': 0,
                        'comment_count': 0,
                        'lines_added': 0
                    }
                repo_stats[repo]['pr_count'] += 1
                repo_stats[repo]['lines_added'] += pr.get('additions', 0)

            for comment in contribs.get('comments', []):
                comment_date_str = comment.get('created_at')
                comment_date = datetime.fromisoformat(comment_date_str).replace(tzinfo=timezone.utc) if comment_date_str else None
                if comment_date and since and comment_date < since:
                    continue
                user = comment['user']
                if user not in contributor_stats:
                    contributor_stats[user] = {
                        'pr_count': 0,
                        'commit_count': 0,
                        'comment_count': 0,
                        'lines_added': 0
                    }
                contributor_stats[user]['comment_count'] += 1

                if repo not in repo_stats:
                    repo_stats[repo] = {
                        'pr_count': 0,
                        'commit_count': 0,
                        'comment_count': 0,
                        'lines_added': 0
                    }
                repo_stats[repo]['comment_count'] += 1

    contributors_df = pd.DataFrame([
        {
            'contributor': user,
            'pr_count': stats['pr_count'],
            'commit_count': stats['commit_count'],
            'comment_count': stats['comment_count'],
            'lines_added': stats['lines_added'],
            'total_contributions': stats['pr_count'] + stats['commit_count'] + stats['comment_count']
        }
        for user, stats in contributor_stats.items()
    ])
    contributors_df.sort_values(by='total_contributions', ascending=False, inplace=True)

    repos_df = pd.DataFrame([
        {
            'repo': repo,
            'pr_count': stats['pr_count'],
            'commit_count': stats['commit_count'],
            'comment_count': stats['comment_count'],
            'lines_added': stats['lines_added'],
            'total_contributions': stats['pr_count'] + stats['commit_count'] + stats['comment_count']
        }
        for repo, stats in repo_stats.items()
    ])
    repos_df.sort_values(by='total_contributions', ascending=False, inplace=True)

    logging.info(f"Aggregated {len(contributors_df)} contributors and {len(repos_df)} repositories.")
    return contributors_df, repos_df

def save_aggregated_data(contributors_df, repos_df, contrib_filename, repos_filename):
    contributors_df.to_csv(contrib_filename, index=False)
    repos_df.to_csv(repos_filename, index=False)
    logging.info(f"Aggregated contributors data saved to '{contrib_filename}'.")
    logging.info(f"Aggregated repositories data saved to '{repos_filename}'.")

def parse_dashboard_aggregation(contrib_file, repo_file, raw_contrib_file):
    contributors_df = pd.read_csv(contrib_file)
    repos_df = pd.read_csv(repo_file)
    contributions = load_contributions_data(raw_contrib_file)
    return contributors_df, repos_df, contributions

def run_dashboard(contributors_df, repos_df, contributions):
    # Load light and dark figure templates
    load_figure_template(["plotly_white", "minty_dark"])

    # Initialize Dash app with Bootstrap theme and Font Awesome icons
    app = Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP, dbc.icons.FONT_AWESOME])
    app.title = "GitHub Contributors Dashboard"

    # Define color mode switch
    color_mode_switch = html.Span(
        [
            dbc.Label(className="fa fa-moon", html_for="switch"),
            dbc.Switch(id="switch", value=False, className="d-inline-block ms-1", persistence=True),
            dbc.Label(className="fa fa-sun", html_for="switch"),
        ]
    )

    # Define time periods
    time_periods = {
        '7 Days': datetime.now(timezone.utc) - timedelta(days=7),
        '30 Days': datetime.now(timezone.utc) - timedelta(days=30),
        '1 Year': datetime.now(timezone.utc) - timedelta(days=365),
        '2 Years': datetime.now(timezone.utc) - timedelta(days=730),
        '4 Years': datetime.now(timezone.utc) - timedelta(days=1460),
        'All Time': datetime(2008, 1, 1, tzinfo=timezone.utc)
    }
    period_options = [{'label': k, 'value': k} for k in time_periods.keys()]

    # Define app layout with dark mode switch
    app.layout = dbc.Container([
        color_mode_switch,
        dcc.Store(id='contributors-data', data=contributors_df.to_dict('records')),
        dcc.Store(id='repos-data', data=repos_df.to_dict('records')),
        dcc.Store(id='raw-contributions', data=contributions),
        dcc.Store(id='theme-store', data='light'),

        dbc.Row(dbc.Col(html.H1("GitHub Org Contributors Dashboard"), className="text-center my-4")),

        dbc.Row([
            dbc.Col([
                html.Label("Select Time Period:"),
                dcc.Dropdown(
                    id='time-period-dropdown',
                    options=period_options,
                    value='All Time',
                    clearable=False
                )
            ], width=4),
        ], justify='center'),

        dbc.Row([
            dbc.Col([
                html.H3("Overall Contributor Leaderboard"),
                dash_table.DataTable(
                    id='contrib-leaderboard-table',
                    columns=None,  # Set dynamically
                    data=[],
                    page_size=10,
                    style_table={'overflowX': 'auto'},
                    sort_action='native',
                    filter_action='native',
                    style_cell={'textAlign': 'left'},
                    style_data={},
                    style_header={},
                ),
                dcc.Graph(id='contrib-bar-chart', className="border")
            ], width=12)
        ]),

        dbc.Row([
            dbc.Col([
                html.H3("Per-Repository Leaderboard"),
                dash_table.DataTable(
                    id='repo-leaderboard-table',
                    columns=None,  # Set dynamically
                    data=[],
                    page_size=10,
                    style_table={'overflowX': 'auto'},
                    sort_action='native',
                    filter_action='native',
                    style_cell={'textAlign': 'left'},
                    style_data={},
                    style_header={}
                ),
                dcc.Graph(id='repo-bar-chart', className="border")
            ], width=12)
        ]),

        dbc.Row([
            dbc.Col([
                html.H3("Contributor Details"),
                dash_table.DataTable(
                    id='drilldown-contrib-table',
                    columns=[
                        {"name": "Repository", "id": "Repository", "presentation": "markdown"},
                        {"name": "PRs", "id": "pr_count"},
                        {"name": "Commits", "id": "commit_count"},
                        {"name": "Comments", "id": "comment_count"},
                        {"name": "Lines Added", "id": "lines_added"},
                        {"name": "Link", "id": "Link", "presentation": "markdown"}
                    ],
                    data=[],
                    page_size=10,
                    sort_action='native',
                    filter_action='native',
                    style_data={},
                    style_header={},
                    style_table={'overflowX': 'auto'},
                    style_cell={'textAlign': 'left'}
                )
            ], width=6),

            dbc.Col([
                html.H3("Repository Details"),
                dash_table.DataTable(
                    id='drilldown-repo-table',
                    columns=[
                        {"name": "Contributor", "id": "Contributor", "presentation": "markdown"},
                        {"name": "PRs", "id": "pr_count"},
                        {"name": "Commits", "id": "commit_count"},
                        {"name": "Comments", "id": "comment_count"},
                        {"name": "Lines Added", "id": "lines_added"},
                        {"name": "Link", "id": "Link", "presentation": "markdown"}
                    ],
                    data=[],
                    page_size=10,
                    sort_action='native',
                    filter_action='native',
                    style_data={},
                    style_header={},
                    style_table={'overflowX': 'auto'},
                    style_cell={'textAlign': 'left'}
                )
            ], width=6),
        ]),

    ], fluid=True)

    @app.callback(
        [
            Output('contrib-leaderboard-table', 'columns'),
            Output('contrib-leaderboard-table', 'data'),
            Output('repo-leaderboard-table', 'columns'),
            Output('repo-leaderboard-table', 'data'),
            Output('contrib-bar-chart', 'figure'),
            Output('repo-bar-chart', 'figure')
        ],
        [
            Input('time-period-dropdown', 'value'),
            Input('switch', 'value')
        ],
        [
            State('raw-contributions', 'data')
        ]
    )
    def update_leaderboards(selected_period, switch_on, raw_contributions):
        since = time_periods[selected_period]
        until = datetime.now(timezone.utc)
        if selected_period == 'All Time':
            since = None

        filtered_contributions = {}
        for repo, contribs in raw_contributions.items():
            filtered_contributions[repo] = {
                'commits': [commit for commit in contribs.get('commits', []) if not since or commit['total_additions'] > 0],
                'prs': [pr for pr in contribs.get('prs', []) if not since or datetime.fromisoformat(pr['created_at']).replace(tzinfo=timezone.utc) >= since],
                'comments': [comment for comment in contribs.get('comments', []) if not since or datetime.fromisoformat(comment['created_at']).replace(tzinfo=timezone.utc) >= since]
            }

        contrib_df, repo_df = parse_and_aggregate(filtered_contributions, since=since, until=until, fetch_slow_data=True)

        contrib_data = contrib_df.copy()
        contrib_data['Contributor'] = contrib_data['contributor'].apply(
            lambda x: f"[{x}](https://github.com/{x})" if x != 'Unknown' else x
        )
        contrib_columns = [
            {"name": "Contributor", "id": "Contributor", "presentation": "markdown"},
            {"name": "PRs", "id": "pr_count"},
            {"name": "Commits", "id": "commit_count"},
            {"name": "Comments", "id": "comment_count"},
            {"name": "Lines Added", "id": "lines_added"},
            {"name": "Total Contributions", "id": "total_contributions"}
        ]
        if not any(contrib_data['pr_count']) and not any(contrib_data['comment_count']):
            contrib_columns = [col for col in contrib_columns if col['id'] not in ['pr_count', 'comment_count']]
        contrib_display = contrib_data.to_dict('records')

        repo_data = repo_df.copy()
        repo_data['Repository'] = repo_data['repo'].apply(
            lambda x: f"[{x}](https://github.com/{x}/pulse)"
        )
        repo_columns = [
            {"name": "Repository", "id": "Repository", "presentation": "markdown"},
            {"name": "PRs", "id": "pr_count"},
            {"name": "Commits", "id": "commit_count"},
            {"name": "Comments", "id": "comment_count"},
            {"name": "Lines Added", "id": "lines_added"},
            {"name": "Total Contributions", "id": "total_contributions"}
        ]
        if not any(repo_data['pr_count']) and not any(repo_data['comment_count']):
            repo_columns = [col for col in repo_columns if col['id'] not in ['pr_count', 'comment_count']]
        repo_display = repo_data.to_dict('records')

        # Determine the template based on the switch state
        template = 'minty_dark' if switch_on else 'plotly_white'

        fig_contrib = px.bar(
            contrib_data.head(10),
            x='contributor',
            y='total_contributions',
            title='Top 10 Contributors by Total Contributions',
            hover_data=['pr_count', 'commit_count', 'comment_count'],
            labels={'total_contributions': 'Total Contributions', 'contributor': 'Contributor'},
            color='contributor',
            color_discrete_sequence=px.colors.qualitative.Set2,
            template=template
        )
        fig_contrib.update_layout(
            xaxis={'categoryorder': 'total descending'},
            font=dict(size=12)
        )

        fig_repo = px.bar(
            repo_data.head(10),
            x='repo',
            y='total_contributions',
            title='Top 10 Repositories by Total Contributions',
            hover_data=['pr_count', 'commit_count', 'comment_count'],
            labels={'total_contributions': 'Total Contributions', 'repo': 'Repository'},
            color='repo',
            color_discrete_sequence=px.colors.qualitative.Set3,
            template=template
        )
        fig_repo.update_layout(
            xaxis={'categoryorder': 'total descending'},
            font=dict(size=12)
        )

        return contrib_columns, contrib_display, repo_columns, repo_display, fig_contrib, fig_repo

    @app.callback(
        [Output('drilldown-contrib-table', 'data'),
         Output('drilldown-repo-table', 'data')],
        [Input('contrib-leaderboard-table', 'active_cell'),
         Input('repo-leaderboard-table', 'active_cell')],
        [State('contrib-leaderboard-table', 'data'),
         State('repo-leaderboard-table', 'data'),
         State('raw-contributions', 'data')]
    )
    def drilldown(active_cell_contrib, active_cell_repo, contrib_data, repo_data, raw_contributions):
        contrib_details = []
        repo_details = []

        if active_cell_contrib:
            row = active_cell_contrib['row']
            contributor_markdown = contrib_data[row]['Contributor']
            contributor = contributor_markdown.split(']')[0].strip('[')
            for repo, contribs in raw_contributions.items():
                pr_count = sum(1 for pr in contribs.get('prs', []) if pr['user'] == contributor)
                commit_count = sum(1 for commit in contribs.get('commits', []) if commit['author'] == contributor)
                comment_count = sum(1 for comment in contribs.get('comments', []) if comment['user'] == contributor)
                lines_added = sum(commit['total_additions'] for commit in contribs.get('commits', []) if commit['author'] == contributor)
                if pr_count > 0 or commit_count > 0 or comment_count > 0:
                    contrib_details.append({
                        'Repository': f"[{repo}](https://github.com/{repo}/pulse)",
                        'pr_count': pr_count,
                        'commit_count': commit_count,
                        'comment_count': comment_count,
                        'lines_added': lines_added,
                        'Link': f"[View PRs](https://github.com/{repo}/pulls?q=author:{contributor})"
                    })

        if active_cell_repo:
            row = active_cell_repo['row']
            repository_markdown = repo_data[row]['Repository']
            repository = repository_markdown.split(']')[0].strip('[')
            contribs_in_repo = raw_contributions.get(repository, {})
            contributors_in_repo = set()
            for commit in contribs_in_repo.get('commits', []):
                contributors_in_repo.add(commit['author'])
            for pr in contribs_in_repo.get('prs', []):
                contributors_in_repo.add(pr['user'])
            for comment in contribs_in_repo.get('comments', []):
                contributors_in_repo.add(comment['user'])
            contributors_in_repo.discard('Unknown')
            for contributor in contributors_in_repo:
                pr_count = sum(1 for pr in contribs_in_repo.get('prs', []) if pr['user'] == contributor)
                commit_count = sum(1 for commit in contribs_in_repo.get('commits', []) if commit['author'] == contributor)
                comment_count = sum(1 for comment in contribs_in_repo.get('comments', []) if comment['user'] == contributor)
                lines_added = sum(commit['total_additions'] for commit in contribs_in_repo.get('commits', []) if commit['author'] == contributor)
                if pr_count > 0 or commit_count > 0 or comment_count > 0:
                    repo_details.append({
                        'Contributor': f"[{contributor}](https://github.com/{contributor})",
                        'pr_count': pr_count,
                        'commit_count': commit_count,
                        'comment_count': comment_count,
                        'lines_added': lines_added,
                        'Link': f"[View PRs](https://github.com/{repository}/pulls?q=author:{contributor})"
                    })

        return contrib_details, repo_details

    # Clientside callback to toggle Bootstrap theme based on switch
    app.clientside_callback(
        """
        function(switchOn) {
            const theme = switchOn ? 'dark' : 'light';
            document.documentElement.setAttribute('data-bs-theme', theme);
            return theme;
        }
        """,
        Output("theme-store", "data"),
        Input("switch", "value"),
    )

    # Add a dummy hidden div for the clientside callback
    app.layout.children.append(html.Div(id='dummy-output', style={'display': 'none'}))

    # Create a callback to update DataTable styles based on theme
    @app.callback(
        [
            Output('contrib-leaderboard-table', 'style_data'),
            Output('contrib-leaderboard-table', 'style_header'),
            Output('repo-leaderboard-table', 'style_data'),
            Output('repo-leaderboard-table', 'style_header'),
            Output('drilldown-contrib-table', 'style_data'),
            Output('drilldown-contrib-table', 'style_header'),
            Output('drilldown-repo-table', 'style_data'),
            Output('drilldown-repo-table', 'style_header'),
        ],
        [Input('theme-store', 'data')]
    )
    def update_table_styles(theme):
        if theme == 'dark':
            data_style = {'backgroundColor': '#333', 'color': 'white'}
            header_style = {'backgroundColor': '#555', 'color': 'white'}
        else:
            data_style = {'backgroundColor': 'white', 'color': 'black'}
            header_style = {'backgroundColor': '#f8f9fa', 'color': 'black'}
        return (
            data_style, header_style,
            data_style, header_style,
            data_style, header_style,
            data_style, header_style,
        )

    app.run_server(debug=True)

def parse_and_save(contrib_file, output_contrib, output_repos, fetch_slow_data=False):
    contributions = load_contributions_data(contrib_file)
    contributors_df, repos_df = parse_and_aggregate(contributions, fetch_slow_data=fetch_slow_data)
    save_aggregated_data(contributors_df, repos_df, output_contrib, output_repos)

def main():
    parser = argparse.ArgumentParser(description='GitHub Private Contributors Report Tool')
    subparsers = parser.add_subparsers(dest='command', required=True, help='Available commands')

    # Fetch Command
    fetch_parser = subparsers.add_parser('fetch', help='Fetch data from GitHub API')
    fetch_parser.add_argument('--orgs', nargs='+', help='List of GitHub organizations to fetch repositories from')
    fetch_parser.add_argument('--output_repos', type=str, default='aggregated_repos_raw.csv', help='Output CSV file for raw repository data')
    fetch_parser.add_argument('--output_contrib', type=str, default='raw_contributions.json', help='Output JSON file for raw contributions data')
    fetch_parser.add_argument('--since', type=str, help='ISO format datetime string to filter contributions since this date (optional)')
    fetch_parser.add_argument('--until', type=str, help='ISO format datetime string to filter contributions until this date (optional)')
    fetch_parser.add_argument('--slow', action='store_true', help='Enable fetching slow data (PRs and comments)')
    fetch_parser.add_argument('--top-n-repos', type=int, help='Fetch top N repositories based on most recent update')
    fetch_parser.add_argument('--exclude-repos', type=str, help='Comma-separated list of substrings to exclude repositories')

    # Parse Command
    parse_parser = subparsers.add_parser('parse', help='Parse and aggregate contributions data')
    parse_parser.add_argument('--contrib_file', type=str, default='raw_contributions.json', help='Path to the raw contributions JSON file')
    parse_parser.add_argument('--output_contrib', type=str, default='aggregated_contributors.csv', help='Output CSV file for aggregated contributors data')
    parse_parser.add_argument('--output_repos', type=str, default='aggregated_repos.csv', help='Output CSV file for aggregated repositories data')
    parse_parser.add_argument('--slow', action='store_true', help='Include slow data (PRs and comments) in aggregation')

    # Dashboard Command
    dashboard_parser = subparsers.add_parser('dashboard', help='Run the interactive dashboard')
    dashboard_parser.add_argument('--contributors', type=str, default='aggregated_contributors.csv', help='Path to the aggregated contributors CSV file')
    dashboard_parser.add_argument('--repositories', type=str, default='aggregated_repos.csv', help='Path to the aggregated repositories CSV file')
    dashboard_parser.add_argument('--raw_contributions', type=str, default='raw_contributions.json', help='Path to the raw contributions JSON file')

    args = parser.parse_args()

    if args.command == 'fetch':
        orgs = args.orgs or ORGS
        if not orgs:
            print("Organizations are required. Provide them via the '--orgs' argument or the 'GITHUB_ORGS' environment variable.")
            sys.exit(1)
        repos = get_repos(orgs, exclude_repos=args.exclude_repos, top_n_repos=args.top_n_repos)
        save_raw_data(repos, args.output_repos)
        since = datetime.fromisoformat(args.since) if args.since else None
        until = datetime.fromisoformat(args.until) if args.until else None
        contributions = {}
        for repo in repos:
            contrib = fetch_contributions(repo.full_name, fetch_slow_data=args.slow, since=since, until=until)
            contributions[repo.full_name] = contrib
        save_contributions_data(contributions, args.output_contrib)

    elif args.command == 'parse':
        parse_and_save(args.contrib_file, args.output_contrib, args.output_repos, fetch_slow_data=args.slow)

    elif args.command == 'dashboard':
        contributors_df, repos_df, contributions = parse_dashboard_aggregation(args.contributors, args.repositories, args.raw_contributions)
        run_dashboard(contributors_df, repos_df, contributions)

if __name__ == '__main__':
    main()
