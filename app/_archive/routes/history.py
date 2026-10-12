@eliquid_mix_bp.route("/production-logs")
@login_required
def production_logs():
    db = get_db()
    search_query = request.args.get('q', '').strip()
    
    # We use a base query and append the WHERE clause if searching
    query_base = """
        SELECT 
            mh.id, 
            mh.batch_number,
            mh.created_at, 
            mh.total_volume_actual as batch_size,
            r.name as recipe_name, 
            rv.version_number,
            u_mixer.username,
            u_sup.username as supervisor_name,
            COALESCE((
                SELECT SUM(mhi.measured_grams * mhi.cost_at_time) 
                FROM mix_history_items mhi 
                WHERE mhi.batch_id = mh.id
            ), 0) as total_cost
        FROM mix_history mh
        JOIN recipes r ON mh.recipe_id = r.id
        JOIN recipe_versions rv ON mh.version_id = rv.id  -- Corrected Join
        JOIN users u_mixer ON mh.user_id = u_mixer.id
        LEFT JOIN users u_sup ON mh.supervisor_id = u_sup.id
    """
    
    params = []
    if search_query:
        query_base += """ WHERE mh.batch_number LIKE ? 
                          OR r.name LIKE ? 
                          OR u_mixer.username LIKE ? """
        like_val = f"%{search_query}%"
        params = [like_val, like_val, like_val]
    
    query_base += " GROUP BY mh.id ORDER BY mh.created_at DESC"
    
    batches = db.execute(query_base, params).fetchall()
    return render_template("production_logs.html", batches=batches, search_query=search_query)