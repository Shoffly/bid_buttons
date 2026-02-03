import streamlit as st
from google.cloud import bigquery
from google.oauth2 import service_account
import pandas as pd
import uuid
from datetime import datetime

# Page configuration
st.set_page_config(
    page_title="Bid Assignment Tool",
    page_icon="🎯",
    layout="wide"
)

# Initialize BigQuery client with credentials from secrets or local file
@st.cache_resource
def get_bq_client():
    """
    Get BigQuery client using credentials from:
    1. Streamlit secrets (for deployed app)
    2. Local service_account.json file (for local development)
    """
    # Try to get credentials from Streamlit secrets first
    try:
        credentials = service_account.Credentials.from_service_account_info(
            st.secrets["service_account"]
        )
        return bigquery.Client(credentials=credentials, project=credentials.project_id)
    except (KeyError, FileNotFoundError):
        pass
    
    # If secret not found, try to use service_account.json
    try:
        credentials = service_account.Credentials.from_service_account_file(
            'service_account.json'
        )
        return bigquery.Client(credentials=credentials, project=credentials.project_id)
    except FileNotFoundError:
        st.error(
            "❌ No credentials found. Please configure either:\n"
            "- Streamlit secrets (for deployment)\n"
            "- A local `service_account.json` file (for development)"
        )
        st.stop()

# The assignment query
ASSIGNMENT_QUERY = """
WITH agents AS (
  SELECT
    ['Zahra','Karim'] AS Buyer_pool_swift,
    ['Dunya','Galal','Mohamed Hasan','Mohamed hanfy'] AS Buyer_pool_cash,
    ['Mohamed Saeed'] AS Buyer_pool_Dealers
),
 
main AS (
  SELECT
    A.bid_id,
    A.cname,
    car.Vehicle_Full_Name__c AS Vehicle_details,
    car.Kilometrage__c AS Kilometrage,
    car.Trim__c AS Trim,
    A.bidder_name,
    A.bidder_phone,
    A.bidder_type,
    A.auctioneer_name AS Seller_name,
    A.auctioneer_phone AS seller_phone,
    A.auctioneer_type AS Seller_type,
 
    A.listing_price,
    A.bid_amount,
    A.bid_seller_amount AS seller_amount,
    A.bid_commission_amount AS commission,
 
    ROUND(SAFE_DIVIDE(A.bid_amount, A.listing_price) * 100, 1) AS Discount_ratio,
 
    CASE
      WHEN ROUND(SAFE_DIVIDE(A.bid_amount, A.listing_price) * 100, 1) >= 95 THEN 'P0'
      WHEN ROUND(SAFE_DIVIDE(A.bid_amount, A.listing_price) * 100, 1) >= 85 THEN 'P1'
      ELSE 'P2'
    END AS Price_priority,
 
    CASE
      WHEN A.accepted_at IS NOT NULL THEN 'ACCEPTED'
      ELSE A.bid_status
    END AS bid_status,
 
    CASE
      WHEN A.follow_up_status IS NULL THEN 'NOT_CONTACTED'
      ELSE A.follow_up_status
    END AS follow_up_status,
 
    DATE(A.bid_created_at) AS bid_created_date,
    A.bid_created_at,
    DATE(A.accepted_at) AS bid_accepted_date,
    A.accepted_at,
    A.followed_up_at,
    A.follow_up_by,
    A.bid_accepted_by
  FROM `pricing-338819.gold_auction.AT_fct_bids` A
  LEFT JOIN (
    SELECT Name, Vehicle_Full_Name__c, Kilometrage__c, Trim__c
    FROM `salesforce.Car__c`
  ) car
    ON car.Name = A.cname
  JOIN `salesforce.Auction__c` auc
    ON A.auction_id = auc.External_Id__c
  WHERE auc.Status__c = 'PUBLISHED'
    AND A.cname NOT IN (
      'C-60777','C-61149','C-60024','C-61060','C-61167','C-60868','C-60968',
      'C-61154','C-61400','C-61223','C-61027','C-61386','C-61316','C-61339',
      'C-61370','C-58625','C-61315','C-61190','C-61307','C-61436','C-61670',
      'C-61585','C-61587','C-61642','C-61601','C-61811','C-56997','C-61669',
      'C-52744','C-61378','C-61736','C-61883','C-60656','C-59274','C-61698',
      'C-62008','C-61717','C-61259','C-61477','C-62135','C-62211','C-61763',
      'C-61606','C-23864','C-61403','C-62245','C-61099','C-42395','C-62131',
      'C-62444','C-62263'
    )
),
 
buyer_phone_counts AS (
  SELECT
    bidder_phone,
    COUNT(*) AS bidder_phone_bid_count
  FROM `pricing-338819.gold_auction.AT_fct_bids`
  GROUP BY bidder_phone
),
 
swift AS (
  SELECT
    phone,
    COUNTIF(approval_stat IS NOT NULL) AS Approved_Applications,
    COUNT(serial_number) AS Applications
  FROM `gold_swift.swift_loan_applications_aggregation`
  WHERE DATE(draft) >= DATE '2025-12-01'
  GROUP BY 1
),
 
data AS (
  SELECT
    m.*,
    pc.bidder_phone_bid_count,
 
    CASE
      WHEN pc.bidder_phone_bid_count = 1 THEN 'low'
      WHEN pc.bidder_phone_bid_count BETWEEN 2 AND 3 THEN 'medium'
      WHEN pc.bidder_phone_bid_count > 3 THEN 'high'
    END AS multiple_bidders_flag,
 
    s.Applications,
    s.Approved_Applications,
 
    CASE WHEN IFNULL(s.Applications, 0) > 0 THEN 'Yes' ELSE 'No' END AS swift_application_flag,
    CASE WHEN IFNULL(s.Approved_Applications, 0) > 0 THEN 'Yes' ELSE 'No' END AS approve_swift_flag
 
  FROM main m
  LEFT JOIN buyer_phone_counts pc
    ON pc.bidder_phone = m.bidder_phone
  LEFT JOIN swift s
    ON s.phone = m.bidder_phone
),
 
scored AS (
  SELECT
    d.*,
 
    CASE
      WHEN approve_swift_flag = 'Yes' AND bidder_type = 'Customer' AND Price_priority = 'P0' THEN 1
      WHEN swift_application_flag = 'Yes' AND bidder_type = 'Customer' AND Price_priority = 'P0' THEN 2
      WHEN bidder_type = 'Dealer' AND Price_priority = 'P0' THEN 3
      WHEN bidder_type = 'Customer' AND multiple_bidders_flag = 'high' AND Price_priority = 'P0' THEN 4
      WHEN bidder_type = 'Customer' AND multiple_bidders_flag = 'medium' AND Price_priority = 'P0' THEN 5
      WHEN bidder_type = 'Customer' AND multiple_bidders_flag = 'low' AND Price_priority = 'P0' THEN 6
 
      WHEN approve_swift_flag = 'Yes' AND bidder_type = 'Customer' AND Price_priority = 'P1' THEN 7
      WHEN swift_application_flag = 'Yes' AND bidder_type = 'Customer' AND Price_priority = 'P1' THEN 8
      WHEN bidder_type = 'Dealer' AND Price_priority = 'P1' THEN 9
      WHEN bidder_type = 'Customer' AND multiple_bidders_flag = 'high' AND Price_priority = 'P1' THEN 10
      WHEN bidder_type = 'Customer' AND multiple_bidders_flag = 'medium' AND Price_priority = 'P1' THEN 11
      WHEN bidder_type = 'Customer' AND multiple_bidders_flag = 'low' AND Price_priority = 'P1' THEN 12
 
      WHEN approve_swift_flag = 'Yes' AND bidder_type = 'Customer' AND Price_priority = 'P2' THEN 13
      WHEN swift_application_flag = 'Yes' AND bidder_type = 'Customer' AND Price_priority = 'P2' THEN 14
      WHEN bidder_type = 'Dealer' AND Price_priority = 'P2' THEN 15
      WHEN bidder_type = 'Customer' AND multiple_bidders_flag = 'high' AND Price_priority = 'P2' THEN 16
      WHEN bidder_type = 'Customer' AND multiple_bidders_flag = 'medium' AND Price_priority = 'P2' THEN 17
      WHEN bidder_type = 'Customer' AND multiple_bidders_flag = 'low' AND Price_priority = 'P2' THEN 18
 
      ELSE 19
    END AS Bids_Assignation_priority,
 
    CASE
      WHEN bid_status = 'ACCEPTED'
        AND follow_up_status = 'NOT_CONTACTED'
        AND (
          (bid_created_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 4 DAY)) -- remove this when bugs are removed
          AND (bid_accepted_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 DAY) OR bid_accepted_date IS NULL)
        ) THEN 1
 
      WHEN bid_status = 'ACCEPTED'
        AND follow_up_status = 'NOT_CONTACTED'
        AND (bid_created_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 4 DAY)) THEN 2
 
      WHEN bid_status = 'PENDING'
        AND follow_up_status = 'NOT_CONTACTED'
        AND (bid_created_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY)) THEN 3
 
      ELSE 4
    END AS bids_timing_priority
 
  FROM data d
),
 
phone_agent_map AS (
  SELECT DISTINCT
    s.bidder_phone,
 
    (
      SELECT Buyer_pool_swift[OFFSET(
        MOD(
          ABS(FARM_FINGERPRINT(CAST(s.bidder_phone AS STRING))),
          ARRAY_LENGTH(Buyer_pool_swift)
        )
      )]
      FROM agents
    ) AS Buyer_pool_swift,
 
    (
      SELECT Buyer_pool_Dealers[OFFSET(
        MOD(
          ABS(FARM_FINGERPRINT(CAST(s.bidder_phone AS STRING))),
          ARRAY_LENGTH(Buyer_pool_Dealers)
        )
      )]
      FROM agents
    ) AS Buyer_pool_Dealers,
 
    (
      SELECT Buyer_pool_cash[OFFSET(
        MOD(
          ABS(FARM_FINGERPRINT(CAST(s.bidder_phone AS STRING))),
          ARRAY_LENGTH(Buyer_pool_cash)
        )
      )]
      FROM agents
    ) AS Buyer_pool_cash
 
  FROM scored s
),
 
final AS (
  SELECT
    s.*,
 
    CASE
      WHEN s.Bids_Assignation_priority IN (1,2,7,8,13,14) THEN pam.Buyer_pool_swift
      WHEN s.Bids_Assignation_priority IN (3,9,15) THEN pam.Buyer_pool_Dealers
      WHEN s.Bids_Assignation_priority IN (4,5,6,10,11,12,16,17,18) THEN pam.Buyer_pool_cash
      ELSE pam.Buyer_pool_cash
    END AS assigned_agent
 
  FROM scored s
  JOIN phone_agent_map pam
    ON pam.bidder_phone = s.bidder_phone
where bids_timing_priority <4
)
 
SELECT
  *,
  CASE
    WHEN Bids_Assignation_priority IN (1,2,7,8,13,14) THEN 'Swift'
    WHEN Bids_Assignation_priority IN (3,9,15) THEN 'Dealers'
    WHEN Bids_Assignation_priority IN (4,5,6,10,11,12,16,17,18) THEN 'Cash'
    ELSE 'NA'
  END AS Bidder_segments
FROM final
ORDER BY
  bids_timing_priority ASC,
  Bids_Assignation_priority ASC,
  Discount_ratio DESC,
  bid_created_at DESC;
"""

# Log table details
LOG_TABLE = "pricing-338819.wholesale_test.bid_assignments_log"


def run_assignment_query(client):
    """Run the assignment query and return results as DataFrame"""
    query_job = client.query(ASSIGNMENT_QUERY)
    results = query_job.result()
    df = results.to_dataframe()
    return df


def log_assignments_to_bq(client, df):
    """Log the assignments to BigQuery"""
    # Add log_id and logged_at columns
    df_to_log = df.copy()
    df_to_log['log_id'] = [str(uuid.uuid4()) for _ in range(len(df))]
    df_to_log['logged_at'] = datetime.utcnow()
    
    # Reorder columns to match schema (log_id and logged_at first)
    cols = ['log_id', 'logged_at'] + [c for c in df_to_log.columns if c not in ['log_id', 'logged_at']]
    df_to_log = df_to_log[cols]
    
    # Configure the load job
    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    
    # Load data to BigQuery
    job = client.load_table_from_dataframe(
        df_to_log, LOG_TABLE, job_config=job_config
    )
    job.result()  # Wait for the job to complete
    
    return len(df_to_log)


def main():
    st.title("🎯 Bid Assignment Tool")
    st.markdown("Run the bid assignment query and log results to BigQuery")
    
    # Initialize session state
    if 'results_df' not in st.session_state:
        st.session_state.results_df = None
    if 'logged' not in st.session_state:
        st.session_state.logged = False
    
    # Sidebar with info
    with st.sidebar:
        st.header("📊 Assignment Info")
        st.markdown("""
        **Priority Levels:**
        - **P0**: Discount ratio ≥ 95%
        - **P1**: Discount ratio ≥ 85%
        - **P2**: Below 85%
        
        **Bidder Segments:**
        - 🏃 Swift: Customers with Swift applications
        - 🏪 Dealers: Dealer bidders
        - 💵 Cash: Regular customers
        
        **Agent Pools:**
        - Seller: Hagar Nazieh
        - Swift: Nada, Zahra, Monira
        - Cash: Dunya, Galal, Mohamed Hasan, Mohamed hanfy
        - Dealers: Mohamed Saeed
        """)
    
    # Main content
    col1, col2 = st.columns([2, 1])
    
    with col1:
        if st.button("🚀 Run Assignment Query", type="primary", use_container_width=True):
            st.session_state.logged = False
            
            with st.spinner("Running assignment query..."):
                try:
                    client = get_bq_client()
                    st.session_state.results_df = run_assignment_query(client)
                    st.success(f"✅ Query completed! Found {len(st.session_state.results_df)} assignments")
                except Exception as e:
                    st.error(f"❌ Error running query: {str(e)}")
    
    with col2:
        if st.session_state.results_df is not None and not st.session_state.logged:
            if st.button("💾 Log to BigQuery", type="secondary", use_container_width=True):
                with st.spinner("Logging assignments to BigQuery..."):
                    try:
                        client = get_bq_client()
                        count = log_assignments_to_bq(client, st.session_state.results_df)
                        st.session_state.logged = True
                        st.success(f"✅ Logged {count} assignments to BigQuery")
                    except Exception as e:
                        st.error(f"❌ Error logging to BigQuery: {str(e)}")
        elif st.session_state.logged:
            st.info("✓ Already logged")
    
    # Display results
    if st.session_state.results_df is not None:
        df = st.session_state.results_df
        
        st.markdown("---")
        
        # Summary metrics
        st.subheader("📈 Summary")
        metric_cols = st.columns(5)
        
        with metric_cols[0]:
            st.metric("Total Bids", len(df))
        
        with metric_cols[1]:
            accepted = len(df[df['bid_status'] == 'ACCEPTED'])
            st.metric("Accepted", accepted)
        
        with metric_cols[2]:
            pending = len(df[df['bid_status'] == 'PENDING'])
            st.metric("Pending", pending)
        
        with metric_cols[3]:
            swift_count = len(df[df['Bidder_segments'] == 'Swift'])
            st.metric("Swift Segment", swift_count)
        
        with metric_cols[4]:
            dealers_count = len(df[df['Bidder_segments'] == 'Dealers'])
            st.metric("Dealers Segment", dealers_count)
        
        # Agent distribution
        st.subheader("👥 Agent Distribution")
        agent_counts = df['assigned_agent'].value_counts()
        
        agent_cols = st.columns(len(agent_counts))
        for i, (agent, count) in enumerate(agent_counts.items()):
            with agent_cols[i]:
                st.metric(agent, count)
        
        # Filters
        st.subheader("🔍 Filter Results")
        filter_cols = st.columns(4)
        
        with filter_cols[0]:
            segment_filter = st.multiselect(
                "Bidder Segment",
                options=df['Bidder_segments'].unique().tolist(),
                default=df['Bidder_segments'].unique().tolist()
            )
        
        with filter_cols[1]:
            status_filter = st.multiselect(
                "Bid Status",
                options=df['bid_status'].unique().tolist(),
                default=df['bid_status'].unique().tolist()
            )
        
        with filter_cols[2]:
            priority_filter = st.multiselect(
                "Price Priority",
                options=df['Price_priority'].unique().tolist(),
                default=df['Price_priority'].unique().tolist()
            )
        
        with filter_cols[3]:
            agent_filter = st.multiselect(
                "Assigned Agent",
                options=df['assigned_agent'].unique().tolist(),
                default=df['assigned_agent'].unique().tolist()
            )
        
        # Apply filters
        filtered_df = df[
            (df['Bidder_segments'].isin(segment_filter)) &
            (df['bid_status'].isin(status_filter)) &
            (df['Price_priority'].isin(priority_filter)) &
            (df['assigned_agent'].isin(agent_filter))
        ]
        
        # Display table
        st.subheader(f"📋 Assignment Results ({len(filtered_df)} records)")
        
        # Select columns to display
        display_cols = [
            'bid_id', 'cname', 'Vehicle_details', 'bidder_name', 'bidder_type',
            'bid_amount', 'listing_price', 'Discount_ratio', 'Price_priority',
            'bid_status', 'Bidder_segments', 'assigned_agent',
            'bids_timing_priority', 'Bids_Assignation_priority'
        ]
        
        # Only include columns that exist in the dataframe
        display_cols = [c for c in display_cols if c in filtered_df.columns]
        
        st.dataframe(
            filtered_df[display_cols],
            use_container_width=True,
            height=500
        )
        
        # Download option
        csv = filtered_df.to_csv(index=False)
        st.download_button(
            label="📥 Download CSV",
            data=csv,
            file_name=f"bid_assignments_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv"
        )


if __name__ == "__main__":
    main()
