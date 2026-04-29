module fifo #(
    parameter WIDTH = 8,
    parameter DEPTH = 16
)(
    input  logic                 clk,
    input  logic                 rst,
    input  logic                 wr_en,
    input  logic                 rd_en,
    input  logic [WIDTH-1:0]     wr_data,
    output logic [WIDTH-1:0]     rd_data,
    output logic                 full,
    output logic                 empty
);

logic [WIDTH-1:0] mem [0:DEPTH-1];
logic [$clog2(DEPTH):0] wr_ptr;
logic [$clog2(DEPTH):0] rd_ptr;

assign empty = (wr_ptr == rd_ptr);
assign full  = (wr_ptr - rd_ptr == DEPTH);  

always_ff @(posedge clk) begin
    if (rst) begin
        wr_ptr <= 0;
        rd_ptr <= 0;
    end
    else begin
        if (wr_en && !full) begin
            mem[wr_ptr[$clog2(DEPTH)-1:0]] <= wr_data;
            wr_ptr <= wr_ptr + 1;
        end

        if (rd_en && !empty) begin
            rd_data <= mem[rd_ptr[$clog2(DEPTH)-1:0]];
            rd_ptr  <= rd_ptr + 1;
        end
    end
end

endmodule
