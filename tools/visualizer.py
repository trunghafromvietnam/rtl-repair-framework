from main_graph import app

def export_graph():
    mermaid_code = app.get_graph().draw_mermaid()
    print("\n--- MERMAID GRAPH CODE ---")
    print(mermaid_code)
    print("--------------------------\n")

    try:
        app.get_graph().draw_mermaid_png(output_file_path="graph.png")
        print("Saved docs to file graph.png")
    except:
        print("NOTE: Install 'pip install pygraphviz' to directly extract graph.")

if __name__ == "__main__":
    export_graph()