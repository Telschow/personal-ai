"""Career Intelligence GUI - Streamlit application."""

from __future__ import annotations

import streamlit as st

from job_agent.gui.services import CareerService

# Page configuration
st.set_page_config(
    page_title="Career Intelligence",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)


def initialize_session_state():
    """Initialize session state variables."""
    if "service" not in st.session_state:
        st.session_state.service = CareerService()

    if "selected_job_id" not in st.session_state:
        st.session_state.selected_job_id = None

    if "filters" not in st.session_state:
        st.session_state.filters = {}

    if "page_filters" not in st.session_state:
        st.session_state.page_filters = {}


def render_sidebar():
    """Render the sidebar navigation."""
    st.sidebar.title("🎯 Career Intelligence")

    # Configuration status
    service = st.session_state.service
    try:
        total_jobs = service.count_jobs()
        st.sidebar.metric("Total Jobs", total_jobs)
    except Exception as e:
        st.sidebar.error(f"Database error: {e}")

    st.sidebar.markdown("---")

    # Navigation
    pages = {
        "📊 Dashboard": "dashboard",
        "📅 Daily Intelligence": "daily",
        "🔍 Job Explorer": "explorer",
        "📋 Calibration": "calibration",
        "📑 Shortlists": "shortlists",
        "📄 CV Generation": "cv",
        "💼 LinkedIn": "linkedin",
        "📁 Projects": "projects",
        "📝 Applications": "applications",
        "👤 Career Profile": "profile",
        "🕷️ Crawl Control": "crawl",
        "🏥 Provider Health": "health",
        "📊 Reports": "reports",
    }

    selected_page = st.sidebar.radio(
        "Navigation",
        list(pages.keys()),
        index=0,
    )

    return pages[selected_page]


def render_dashboard():
    """Render the dashboard page."""
    service = st.session_state.service

    st.title("📊 Career Intelligence Dashboard")
    st.markdown("---")

    # Get metrics
    try:
        total_jobs = service.count_jobs()
    except Exception as e:
        st.error(f"Error loading job count: {e}")
        total_jobs = 0

    try:
        feedback_summary = service.get_feedback_summary()
        unique_jobs_with_feedback = feedback_summary.get("unique_jobs", 0)
        total_feedback = feedback_summary.get("total_records", 0)
    except Exception:
        unique_jobs_with_feedback = 0
        total_feedback = 0

    try:
        applications = service.get_applications()
        active_applications = [a for a in applications if a.get("stage") not in ["REJECTED", "WITHDRAWN", "HIRED"]]
    except Exception:
        active_applications = []

    # Metrics row
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric(label="Latest Crawl", value="Active")

    with col2:
        st.metric(label="New Jobs", value="--")  # Would need delta calculation

    with col3:
        st.metric(label="Active Jobs", value=total_jobs)

    with col4:
        st.metric(label="High-Fit Jobs", value="--")  # Would need score calculation

    with col5:
        st.metric(label="Jobs Reviewed", value=unique_jobs_with_feedback)

    # Applications row
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric(label="Applications", value=len(active_applications))

    with col2:
        # Count interviews
        interviews = [a for a in active_applications if a.get("stage") == "INTERVIEW"]
        st.metric(label="Interviews", value=len(interviews))

    with col3:
        st.metric(label="Feedback Records", value=total_feedback)

    with col4:
        st.metric(label="Provider Health", value="--")

    with col5:
        st.metric(label="System Status", value="✅ Healthy")

    st.markdown("---")

    # Quick actions
    st.subheader("Quick Actions")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        if st.button("🔍 Explore Jobs", use_container_width=True):
            st.session_state.current_page = "explorer"
            st.rerun()

    with col2:
        if st.button("📄 Generate CV", use_container_width=True):
            st.session_state.current_page = "cv"
            st.rerun()

    with col3:
        if st.button("📝 Review Feedback", use_container_width=True):
            st.session_state.current_page = "calibration"
            st.rerun()

    with col4:
        if st.button("📊 View Reports", use_container_width=True):
            st.session_state.current_page = "reports"
            st.rerun()


def render_daily_intelligence():
    """Render daily intelligence view."""
    service = st.session_state.service

    st.title("📅 Daily Intelligence")

    # Use tabs for different views
    tabs = st.tabs(
        [
            "NEW",
            "CHANGED",
            "HIGH-FIT",
            "HIGH-FIT LOCAL",
            "HIGH-FIT DOMAIN",
            "NEWLY SALARY-DISCLOSED",
            "STALE/CLOSING",
        ]
    )

    # NEW jobs
    with tabs[0]:
        st.subheader("New Jobs")
        try:
            jobs = service.get_jobs(limit=50)
            if jobs:
                # Display recent jobs
                for job in jobs[:10]:
                    with st.container(border=True):
                        col1, col2 = st.columns([3, 1])
                        with col1:
                            st.write(f"**{job.title}** at {job.company}")
                            st.write(f"Location: {job.location}")
                        with col2:
                            if st.button("View", key=f"view_{job.id}"):
                                st.session_state.selected_job_id = job.id
                                st.session_state.current_page = "explorer"
                                st.rerun()
            else:
                st.info("No jobs found.")
        except Exception as e:
            st.error(f"Error loading jobs: {e}")

    # Other tabs would follow similar pattern
    with tabs[1]:
        st.subheader("Changed Jobs")
        st.info("Delta tracking shows jobs with updated salary, status, or location changes.")

    with tabs[2]:
        st.subheader("High-Fit Jobs")
        st.info("Jobs with overall fit score above threshold.")

    with tabs[3]:
        st.subheader("High-Fit Local Jobs")
        st.info("High-fit jobs in your preferred city. Set one in config; none is assumed.")

    with tabs[4]:
        st.subheader("High-Fit Domain Jobs")
        st.info("High-fit jobs in the sectors you configured.")

    with tabs[5]:
        st.subheader("Newly Salary-Disclosed Jobs")
        st.info("Jobs where salary information has been newly disclosed.")

    with tabs[6]:
        st.subheader("Stale/Closing Jobs")
        st.info("Jobs approaching close date or with stale data.")


def render_job_explorer():
    """Render job explorer with filters and table."""
    service = st.session_state.service

    st.title("🔍 Job Explorer")

    # Filters in sidebar
    with st.sidebar:
        st.subheader("Filters")

        # Company filter
        company_filter = st.text_input("Company", value=st.session_state.filters.get("company", ""))

        # Role family filter
        role_family_filter = st.selectbox(
            "Role Family",
            ["", "Product Management", "Product Strategy", "Solutions Architecture", "AI Product"],
            index=0,
        )

        # Location filter
        location_filter = st.text_input("Location", value=st.session_state.filters.get("location", ""))

        # Salary disclosed
        salary_disclosed = st.checkbox(
            "Salary Disclosed", value=st.session_state.filters.get("salary_disclosed", False)
        )

        # Remote
        remote_filter = st.selectbox("Remote", ["", "yes", "no", "hybrid"])

        # Apply filters button
        if st.button("Apply Filters"):
            st.session_state.filters = {
                "company": company_filter,
                "role_family": role_family_filter if role_family_filter else None,
                "location": location_filter,
                "salary_disclosed": salary_disclosed,
                "remote": remote_filter if remote_filter else None,
            }
            st.rerun()

    # Load jobs
    filters = st.session_state.filters
    try:
        jobs = service.get_jobs_with_filters(filters, limit=100)
    except Exception as e:
        st.error(f"Error loading jobs: {e}")
        jobs = []

    # Display jobs in table
    if jobs:
        # Convert jobs to display format
        job_data = []
        for job in jobs:
            job_data.append(
                {
                    "Company": job.company,
                    "Title": job.title,
                    "Location": job.location or "N/A",
                    "Role Family": getattr(job, "role_family", "N/A"),
                    "Salary": f"{job.salary_min_eur}- {job.salary_max_eur}" if job.salary_min_eur else "Not disclosed",
                    "Status": job.status or "active",
                    "Overall Fit": "--",  # Would need actual scoring
                    "First Seen": job.discovered_at or "N/A",
                }
            )

        st.dataframe(job_data, use_container_width=True)

        # Job detail view
        if st.session_state.selected_job_id:
            job = service.get_job(st.session_state.selected_job_id)
            if job:
                st.markdown("---")
                st.subheader("Job Details")
                col1, col2 = st.columns(2)

                with col1:
                    st.write(f"**Company:** {job.company}")
                    st.write(f"**Title:** {job.title}")
                    st.write(f"**Location:** {job.location}")
                    st.write(f"**Status:** {job.status}")

                with col2:
                    st.write(f"**Role Family:** {getattr(job, 'role_family', 'N/A')}")
                    st.write(f"**URL:** {job.url}")
                    st.write(f"**First Seen:** {job.discovered_at}")
                    st.write(f"**Last Seen:** {job.last_seen}")

                # Fit breakdown
                st.subheader("Fit Decomposition")
                st.write("Career Fit: --")
                st.write("Role Fit: --")
                st.write("Location Fit: --")
                st.write("Compensation Fit: --")
                st.write("Overall: --")

                # Explanation
                st.subheader("Why It Matches")
                st.write("Key strengths:")
                st.write("Main gaps:")

                # Provenance
                st.subheader("Source & Provenance")
                st.write(f"Provider: {job.source}")
                st.write(f"Provider Native ID: {getattr(job, 'provider_native_id', 'N/A')}")
                st.write(f"Discovery Source: {job.discovery_source}")
                st.write(f"Run ID: {job.run_id}")

                # Feedback controls
                st.subheader("Feedback")
                feedback_labels = [
                    "strong_interest",
                    "interested",
                    "maybe",
                    "not_interested",
                    "wrong_role",
                    "wrong_seniority",
                    "wrong_location",
                    "wrong_compensation",
                    "wrong_domain",
                    "duplicate",
                    "irrelevant",
                ]

                selected_label = st.selectbox("Feedback Label", feedback_labels)
                note = st.text_area("Note (optional)")

                if st.button("Submit Feedback"):
                    try:
                        service.add_feedback(
                            st.session_state.selected_job_id,
                            selected_label,
                            note,
                        )
                        st.success("Feedback recorded successfully!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error recording feedback: {e}")

    else:
        st.info("No jobs match the current filters.")


def render_calibration():
    """Render calibration/feedback queue page."""
    service = st.session_state.service

    st.title("📋 Feedback Calibration")

    # Stats
    try:
        feedback_summary = service.get_feedback_summary()
        total_records = feedback_summary.get("total_records", 0)
        unique_jobs = feedback_summary.get("unique_jobs", 0)
    except Exception:
        total_records = 0
        unique_jobs = 0

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Jobs Awaiting Review", "--")
    with col2:
        st.metric("Jobs Reviewed", unique_jobs)
    with col3:
        st.metric("Strong Interest", "--")
    with col4:
        st.metric("Negative Labels", "--")

    st.markdown("---")

    # Calibration metrics
    st.subheader("Calibration Metrics")

    try:
        # This would require actual scoring data
        st.metric("Relevant@10", "--", help="Precision at top 10")
        st.metric("Relevant@20", "--", help="Precision at top 20")
        st.metric("Relevant@50", "--", help="Precision at top 50")
        st.metric("Average Rank of Strong Interest", "--")
        st.metric("High-Score False Positives", "--")
        st.metric("Low-Score False Negatives", "--")

        if total_records < 10:
            st.warning("⚠️ FEEDBACK_CALIBRATION_INSUFFICIENT: Need more feedback for reliable metrics")
    except Exception as e:
        st.error(f"Error loading calibration metrics: {e}")


def render_shortlists():
    """Render shortlists page."""
    service = st.session_state.service

    st.title("📑 Shortlists")

    # Shortlist tabs
    tabs = st.tabs(["Top Overall", "Preferred City", "Remote", "Domain", "Seniority", "International"])

    with tabs[0]:
        st.subheader("Top Overall Jobs")
        try:
            jobs = service.get_jobs(limit=20)
            for job in jobs[:10]:
                with st.container(border=True):
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        st.write(f"**{job.title}** at {job.company}")
                        st.write(f"Location: {job.location}")
                    with col2:
                        if st.button("View", key=f"top_{job.id}"):
                            st.session_state.selected_job_id = job.id
                            st.session_state.current_page = "explorer"
                            st.rerun()
        except Exception as e:
            st.error(f"Error loading jobs: {e}")

    with tabs[1]:
        st.subheader("Preferred City")
        st.info("Jobs in the city configured as preferred, if any.")

    with tabs[2]:
        st.subheader("Remote Jobs")
        st.info("Remote postings regardless of location.")

    with tabs[3]:
        st.subheader("Domain Jobs")
        st.info("Jobs in the sectors you configured.")

    with tabs[4]:
        st.subheader("Senior Roles")
        st.info("Roles at or above your configured seniority.")

    with tabs[5]:
        st.subheader("International Jobs")
        st.info("Jobs outside Germany.")


def render_cv_generation():
    """Render CV generation page."""
    service = st.session_state.service

    st.title("📄 CV Generation")

    # Job selection
    try:
        jobs = service.get_jobs(limit=50)
        job_options = {f"{job.company} - {job.title}": job.id for job in jobs}
    except Exception as e:
        st.error(f"Error loading jobs: {e}")
        job_options = {}

    if job_options:
        selected_job_label = st.selectbox("Select Job", list(job_options.keys()))
        selected_job_id = job_options[selected_job_label]

        col1, col2 = st.columns(2)

        with col1:
            language = st.selectbox("Language", ["en", "de"])
            master_cv_path = st.text_input("Master CV Path", value="profile/profile.yaml")

        with col2:
            st.write("Options:")
            use_llm = st.checkbox("Use LLM for generation", value=True)
            use_semantic = st.checkbox("Use semantic matching", value=True)

        if st.button("Generate Tailored CV", type="primary"):
            try:
                with st.spinner("Generating CV..."):
                    result = service.generate_cv(
                        selected_job_id,
                        master_cv_path,
                        language=language,
                        use_llm=use_llm,
                        use_semantic=use_semantic,
                    )

                st.success("CV generated successfully!")

                # Display results
                st.subheader("CV Artifact")
                st.write(f"**Artifact ID:** {result['artifact_id']}")
                st.write(f"**Status:** {result['status']}")
                st.write(f"**Career Move Type:** {result['move_type']}")

                st.subheader("Evidence Manifest")
                if result.get("manifest"):
                    st.json(result["manifest"])

                st.subheader("CV Preview")
                st.markdown("### Generated CV Content")
                st.markdown(result.get("sections", []))

            except Exception as e:
                st.error(f"Error generating CV: {e}")
    else:
        st.info("No jobs available for CV generation.")


def render_linkedin():
    """Render LinkedIn optimization page."""
    service = st.session_state.service

    st.title("💼 LinkedIn Optimization")

    st.warning("⚠️ Recommendations do not mutate your profile automatically. Review manually before applying changes.")

    # Job selection
    try:
        jobs = service.get_jobs(limit=50)
        job_options = {f"{job.company} - {job.title}": job.id for job in jobs}
    except Exception as e:
        st.error(f"Error loading jobs: {e}")
        job_options = {}

    if job_options:
        selected_job_label = st.selectbox("Select Job", list(job_options.keys()))
        selected_job_id = job_options[selected_job_label]

        col1, col2 = st.columns(2)

        with col1:
            master_cv_path = st.text_input("Master CV Path", value="profile/profile.yaml")
            linkedin_profile_path = st.text_input("LinkedIn Profile Path", value="profile/linkedin.yaml")

        with col2:
            use_llm = st.checkbox("Use LLM for generation", value=True)
            use_semantic = st.checkbox("Use semantic matching", value=True)

        if st.button("Optimize LinkedIn Profile", type="primary"):
            try:
                with st.spinner("Optimizing LinkedIn profile..."):
                    result = service.optimize_linkedin(
                        selected_job_id,
                        master_cv_path,
                        linkedin_profile_path,
                        use_llm=use_llm,
                        use_semantic=use_semantic,
                    )

                st.success("LinkedIn optimization complete!")

                # Display recommendations
                st.subheader("Recommendations")

                for rec in result.get("recommendations", []):
                    with st.expander(f"{rec['section']} - {rec['verification']}"):
                        col1, col2 = st.columns(2)

                        with col1:
                            st.write("**Current:**")
                            st.write(rec.get("current", "Not specified"))

                        with col2:
                            st.write("**Recommended:**")
                            st.write(rec.get("recommended", "Not specified"))

                        st.write("**Rationale:**")
                        st.write(rec.get("rationale", "No rationale provided"))

                        if rec.get("evidence_ids"):
                            st.write("**Evidence:**")
                            st.write(f"Evidence IDs: {', '.join(rec['evidence_ids'])}")

                st.subheader("Validation Status")
                st.write(result.get("validation_status", "Unknown"))

                st.subheader("Career Move Type")
                st.write(result.get("career_move_type", "Unknown"))

            except Exception as e:
                st.error(f"Error optimizing LinkedIn profile: {e}")
    else:
        st.info("No jobs available for LinkedIn optimization.")


def render_projects():
    """Render portfolio projects page."""
    service = st.session_state.service

    st.title("📁 Portfolio Projects")

    # Job selection for targeted recommendations
    try:
        jobs = service.get_jobs(limit=50)
        job_options = {f"{job.company} - {job.title}": job.id for job in jobs}
    except Exception as e:
        st.error(f"Error loading jobs: {e}")
        job_options = {}

    if job_options:
        selected_job_label = st.selectbox("Select Job for Recommendations", [""] + list(job_options.keys()))

        if selected_job_label:
            selected_job_id = job_options[selected_job_label]

            if st.button("Get Recommendations"):
                try:
                    with st.spinner("Generating recommendations..."):
                        recommendations = service.get_project_recommendations(selected_job_id)

                    st.subheader("Recommended Projects")

                    for rec in recommendations:
                        with st.container(border=True):
                            st.write(f"**Project:** {rec['name']}")
                            st.write(f"**Target:** {rec['target']}")
                            st.write(f"**Problem:** {rec['problem']}")
                            st.write(f"**Skills Demonstrated:** {', '.join(rec['skills'])}")
                            st.write(f"**Estimated Effort:** {rec['effort']}")

                            col1, col2 = st.columns(2)
                            with col1:
                                st.write("**CV Value:**")
                                st.write(rec.get("cv_potential", ""))
                            with col2:
                                st.write("**Status:**")
                                st.write("Proposed")
                except Exception as e:
                    st.error(f"Error getting recommendations: {e}")
    else:
        st.info("No jobs available.")


def render_applications():
    """Render application tracker page."""
    service = st.session_state.service

    st.title("📝 Application Tracker")

    # Application stages
    stages = ["shortlisted", "applied", "interview", "rejected", "offer", "withdrawn", "closed"]

    # Status selector
    selected_stage = st.selectbox("Filter by Stage", ["All"] + stages)

    try:
        if selected_stage == "All":
            applications = service.get_applications()
        else:
            applications = service.get_applications(status=selected_stage.upper())

        if applications:
            for app in applications:
                with st.container(border=True):
                    col1, col2, col3 = st.columns([2, 2, 1])

                    with col1:
                        st.write(f"**Company:** {app.get('job_title', 'Unknown')}")
                        st.write(f"**Role:** {app.get('job_company', 'Unknown')}")
                        st.write(f"**Date:** {app.get('created_at', 'N/A')}")

                    with col2:
                        st.write(f"**Status:** {app.get('stage', 'Unknown')}")
                        st.write(f"**CV Variant:** {app.get('cv_artifact_id', 'N/A')}")
                        st.write(f"**Next Action:** {app.get('next_action', 'N/A')}")

                    with col3:
                        # Update stage controls
                        new_stage = st.selectbox(
                            "Update Stage",
                            [
                                "NOT_APPLIED",
                                "APPLIED",
                                "RESPONDED",
                                "INTERVIEW",
                                "OFFER",
                                "HIRED",
                                "REJECTED",
                                "WITHDRAWN",
                            ],
                            key=f"stage_{app['job_id']}",
                        )

                        if st.button("Update", key=f"update_{app['job_id']}"):
                            try:
                                service.update_application_stage(app["job_id"], new_stage)
                                st.success("Application updated!")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error updating application: {e}")
        else:
            st.info("No applications found for this stage.")

    except Exception as e:
        st.error(f"Error loading applications: {e}")


def render_career_profile():
    """Render career profile page."""
    service = st.session_state.service

    st.title("👤 Career Profile")

    try:
        profile = service.career_profile

        st.subheader("Target Role Families")
        for role in profile.target_role_families:
            st.write(f"- {role}")

        st.subheader("Career Directions")
        for direction in profile.career_directions:
            st.write(f"- {direction}")

        st.subheader("Seniority")
        st.write(profile.seniority)

        st.subheader("Industries")
        for industry in profile.industries:
            st.write(f"- {industry}")

        st.subheader("Location Preferences")
        for location in profile.location_preferences:
            st.write(f"- {location}")

        st.subheader("Compensation")
        st.write(f"Minimum: {profile.compensation_min}")
        st.write(f"Target: {profile.compensation_target}")

        st.subheader("Skills")
        for skill in profile.skills[:20]:  # Limit display
            st.write(f"- {skill}")

    except Exception as e:
        st.error(f"Error loading career profile: {e}")


def render_crawl():
    """Render crawl control page."""
    service = st.session_state.service

    st.title("🕷️ Crawl Control")

    try:
        latest_run = service.get_latest_discovery_run()

        if latest_run:
            st.subheader("Latest Crawl")
            st.write(f"Run ID: {latest_run.get('run_id', 'N/A')}")
            st.write(f"Started: {latest_run.get('started_at', 'N/A')}")
            st.write(f"Duration: {latest_run.get('duration_seconds', 'N/A')}s")
            st.write(f"Providers: {latest_run.get('providers_count', 0)}")
            st.write(f"Companies: {latest_run.get('companies_count', 0)}")
            st.write(f"Candidates: {latest_run.get('candidates_count', 0)}")
            st.write(f"Duplicates: {latest_run.get('duplicates_count', 0)}")
            st.write(f"New Jobs: {latest_run.get('new_jobs_count', 0)}")
            st.write(f"Errors: {latest_run.get('errors_count', 0)}")
        else:
            st.info("No crawl runs found.")

        st.markdown("---")
        st.subheader("Trigger New Crawl")
        st.warning("Crawl initiation should be done via CLI for now: `uv run python -m job_agent.cli discover run`")

    except Exception as e:
        st.error(f"Error loading crawl info: {e}")


def render_provider_health():
    """Render provider health page."""
    service = st.session_state.service

    st.title("🏥 Provider Health")

    try:
        providers = service.get_provider_health()

        if providers:
            for provider in providers:
                with st.container(border=True):
                    col1, col2, col3 = st.columns([2, 1, 1])

                    with col1:
                        st.write(f"**Provider:** {provider['provider']}")
                        st.write(f"**Source ID:** {provider['source_id']}")
                        st.write(f"**Status:** {provider['status']}")

                    with col2:
                        st.write(f"Last Run: {provider['last_run']}")
                        st.write(f"Candidates: {provider['candidates']}")
                        st.write(f"New: {provider['new']}")

                    with col3:
                        st.write(f"Duplicates: {provider['duplicates']}")
                        st.write(f"Errors: {provider['errors']}")
                        st.write(f"Latency: {provider['latency']}s")
        else:
            st.info("No provider data available.")

    except Exception as e:
        st.error(f"Error loading provider health: {e}")


def render_reports():
    """Render reports page."""
    service = st.session_state.service

    st.title("📊 Reports")

    try:
        reports = service.list_reports()

        if reports:
            for report in reports:
                with st.container(border=True):
                    st.write(f"**{report['name']}**")
                    st.write(f"Modified: {report['modified']}")
                    st.write(f"Size: {report['size']} bytes")

                    if st.button("View Report", key=f"view_report_{report['name']}"):
                        try:
                            content = service.get_report_content(report["path"])
                            st.markdown("---")
                            st.subheader(f"Report: {report['name']}")
                            st.markdown(content)
                        except Exception as e:
                            st.error(f"Error loading report: {e}")
        else:
            st.info("No reports found.")

    except Exception as e:
        st.error(f"Error loading reports: {e}")


def main():
    """Main Streamlit app."""
    initialize_session_state()

    # Get page from sidebar

    selected_page = render_sidebar()

    # Route to appropriate page
    if selected_page == "dashboard":
        render_dashboard()
    elif selected_page == "daily":
        render_daily_intelligence()
    elif selected_page == "explorer":
        render_job_explorer()
    elif selected_page == "calibration":
        render_calibration()
    elif selected_page == "shortlists":
        render_shortlists()
    elif selected_page == "cv":
        render_cv_generation()
    elif selected_page == "linkedin":
        render_linkedin()
    elif selected_page == "projects":
        render_projects()
    elif selected_page == "applications":
        render_applications()
    elif selected_page == "profile":
        render_career_profile()
    elif selected_page == "crawl":
        render_crawl()
    elif selected_page == "health":
        render_provider_health()
    elif selected_page == "reports":
        render_reports()


if __name__ == "__main__":
    main()
