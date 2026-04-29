module alu(
    input  logic [31:0] a,
    input  logic [31:0] b,
    input  logic [1:0]  op,
    output logic [31:0] result,
    output logic        zero
);

always_comb begin
    case (op)
        2'b00: result = a + b;
        2'b01: result = a - b;
        2'b10: result = a & b;
        2'b11: result = a & b; 
    endcase
end

assign zero = (result == 32'd0);

endmodule
